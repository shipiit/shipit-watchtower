"""
Prompt registry — fetch managed prompts from Langfuse, with a safety net.

This is the other half of :mod:`shipit_watcher.identity`. Identity answers
*"which prompt produced this answer"*; the registry answers *"which prompt
should we be using"*, and together they make governance possible: a prompt
resolved from the registry is `registered=True`, everything else shows up in
the compliance gap report.

Three properties matter more than the API surface:

**A registry outage must not take the product down.** Prompts are on the
critical path of every request. So resolution is cached, the cache is served
stale on failure, and a caller-supplied fallback is the last resort. The
product keeps answering; the trace records that it ran on a fallback.

**Cache TTL is a correctness decision, not a performance one.** Too long and a
prompt rollback takes minutes to take effect. The default is 60s, which bounds
how long a bad prompt can stay live after someone reverts it.

**Compilation is explicit.** ``{{variable}}`` placeholders are substituted
here rather than by the caller, so the *template* is what gets fingerprinted —
otherwise every request would look like a different prompt version.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

from .config import get_config
from .identity import PromptIdentity, fingerprint_text

logger = logging.getLogger(__name__)

__all__ = [
    "ManagedPrompt", "PromptRegistry", "get_registry",
    "get_prompt", "create_prompt", "get_agent_prompt", "agent_prompt_name",
]

#: Langfuse's template syntax.
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

#: How long a resolved prompt is reused before re-fetching.
DEFAULT_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class ManagedPrompt:
    """A prompt resolved from the registry (or a fallback standing in for one)."""

    name: str
    template: str
    version: Optional[str] = None
    labels: tuple[str, ...] = ()
    config: Dict[str, Any] = field(default_factory=dict)
    #: False when this came from a fallback rather than the registry. Drives
    #: the compliance gap report, so it must never default to True.
    registered: bool = False
    #: True when served from cache after a failed refresh — the product kept
    #: working, but the value may be behind the registry.
    stale: bool = False

    def compile(self, **variables: Any) -> str:
        """Substitute ``{{placeholders}}``.

        Unknown placeholders are left intact rather than raising: a missing
        variable should degrade the answer, not 500 the request. The gap is
        visible in the trace because the raw ``{{name}}`` survives into the
        recorded prompt.
        """
        def replace(match: "re.Match[str]") -> str:
            key = match.group(1)
            return str(variables[key]) if key in variables else match.group(0)

        return _PLACEHOLDER.sub(replace, self.template)

    @property
    def identity(self) -> PromptIdentity:
        """Identity for the *template*, not the compiled text.

        Fingerprinting after compilation would make every request a new
        version, which defeats grouping traces by prompt.
        """
        return PromptIdentity(
            name=self.name,
            fingerprint=fingerprint_text(self.template),
            version=self.version,
            registered=self.registered,
        )

    def as_metadata(self) -> Dict[str, Any]:
        data = self.identity.as_metadata()
        data["prompt_stale"] = self.stale
        return data



def _is_not_found(exc: Exception) -> bool:
    """Whether *exc* means "no such prompt" rather than "registry is broken"."""
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    return "not found" in str(exc)[:200].lower()


def _brief(exc: Exception) -> str:
    """A one-line summary. The Langfuse SDK attaches whole HTML pages to its
    errors, and a log line is not the place for one."""
    text = " ".join(str(exc).split())
    return text[:200] + ("…" if len(text) > 200 else "")


@dataclass
class _CacheEntry:
    prompt: ManagedPrompt
    fetched_at: float

    def is_fresh(self, ttl: float) -> bool:
        return (time.time() - self.fetched_at) < ttl


class PromptRegistry:
    """Resolves prompts from Langfuse Prompt Management.

    Thread-safe and process-local. The lock guards the cache dict only — never
    the network call, so a slow registry cannot serialise every request behind
    a single fetch.
    """

    def __init__(self, client: Any = None, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self._client = client
        self._ttl = ttl_seconds
        self._cache: Dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    # -- client ---------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        config = get_config()
        if not config.has_langfuse_credentials:
            return None
        try:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=config.langfuse_public_key,
                secret_key=config.langfuse_secret_key,
                host=config.langfuse_host,
            )
        except Exception:
            logger.warning("watcher: prompt registry client unavailable", exc_info=True)
            self._client = None
        return self._client

    # -- resolution -----------------------------------------------------

    @staticmethod
    def _cache_key(name: str, version: Optional[str], label: Optional[str]) -> str:
        return f"{name}::{version or ''}::{label or ''}"

    def get(
        self,
        name: str,
        *,
        version: Optional[str] = None,
        label: Optional[str] = "production",
        fallback: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> ManagedPrompt:
        """Resolve a prompt.

        Order: fresh cache → registry → **stale cache** → fallback.

        The stale-cache step is the important one. If Langfuse is unreachable,
        serving a slightly old prompt is strictly better than failing the
        request or silently dropping to a hard-coded string that may be months
        behind.

        ``label`` selects a deployment channel (``production``, ``staging``),
        which is how a promotion workflow takes effect without a code change.
        """
        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        key = self._cache_key(name, version, label)

        with self._lock:
            entry = self._cache.get(key)
            if entry and entry.is_fresh(ttl):
                return entry.prompt

        fetched = self._fetch(name, version=version, label=label)
        if fetched is not None:
            with self._lock:
                self._cache[key] = _CacheEntry(fetched, time.time())
            return fetched

        # Registry unreachable — prefer a stale value over no value.
        with self._lock:
            entry = self._cache.get(key)
        if entry is not None:
            logger.warning("watcher: serving stale prompt %r", name)
            return ManagedPrompt(
                name=entry.prompt.name, template=entry.prompt.template,
                version=entry.prompt.version, labels=entry.prompt.labels,
                config=entry.prompt.config, registered=entry.prompt.registered,
                stale=True,
            )

        logger.warning("watcher: prompt %r unresolved, using fallback", name)
        resolved = ManagedPrompt(
            name=name,
            template=fallback or "",
            registered=False,   # a fallback is by definition unregistered
        )
        # Cache the fallback too. A prompt that is missing from the registry is
        # missing for every request, so without this each one pays a network
        # round-trip (and a 404) to learn the same thing. The TTL still bounds
        # how long it takes to notice the prompt being added.
        with self._lock:
            self._cache[key] = _CacheEntry(resolved, time.time())
        return resolved

    def _fetch(self, name: str, *, version: Optional[str],
               label: Optional[str]) -> Optional[ManagedPrompt]:
        client = self._get_client()
        if client is None:
            return None
        try:
            kwargs: Dict[str, Any] = {}
            if version:
                kwargs["version"] = int(version) if str(version).isdigit() else version
            elif label:
                kwargs["label"] = label

            raw = client.get_prompt(name, **kwargs)
            template = getattr(raw, "prompt", None)
            if template is None:
                return None
            # Chat prompts arrive as a message list; join for fingerprinting.
            if isinstance(template, list):
                template = "\n".join(
                    str(m.get("content", "")) if isinstance(m, dict) else str(m)
                    for m in template
                )

            return ManagedPrompt(
                name=name,
                template=str(template),
                version=str(getattr(raw, "version", "") or "") or None,
                labels=tuple(getattr(raw, "labels", ()) or ()),
                config=dict(getattr(raw, "config", {}) or {}),
                registered=True,
            )
        except Exception as exc:
            # A prompt that is simply not in the registry is an expected state
            # — it is how the fallback ladder is meant to work — and the
            # Langfuse SDK reports it by raising with the server's entire HTML
            # 404 page attached. Logging that at WARNING with a traceback
            # buried every other line in the log.
            if _is_not_found(exc):
                logger.info("watcher: prompt %r is not in the registry", name)
            else:
                logger.warning("watcher: could not fetch prompt %r: %s",
                               name, _brief(exc))
            return None

    # -- authoring ------------------------------------------------------

    def create(
        self,
        name: str,
        template: Any,
        *,
        labels: Sequence[str] = ("production",),
        tags: Sequence[str] = (),
        config: Optional[Dict[str, Any]] = None,
        commit_message: Optional[str] = None,
    ) -> ManagedPrompt:
        """Publish a new version of a prompt and return it.

        Langfuse versions prompts by name: calling this again with the same
        name never overwrites, it appends version *n+1*. ``labels`` is what
        actually decides which version serves traffic — publishing without
        ``production`` stages the prompt for review rather than releasing it.

        ``template`` may be a string or a list of chat messages; the type is
        inferred so callers do not have to name it twice.

        Unlike :meth:`get`, this raises. A failed write is a deployment that
        did not happen, and silently returning a local object would let a
        release script report success having changed nothing.
        """
        client = self._get_client()
        if client is None:
            raise RuntimeError(
                "watcher: no Langfuse client — set LANGFUSE_PUBLIC_KEY, "
                "LANGFUSE_SECRET_KEY and LANGFUSE_HOST before creating prompts."
            )

        is_chat = isinstance(template, list)
        raw = client.create_prompt(
            name=name,
            prompt=template,
            type="chat" if is_chat else "text",
            labels=list(labels),
            tags=list(tags) or None,
            config=config or {},
            commit_message=commit_message,
        )

        # The freshly published version supersedes anything cached under this
        # name, including a fallback cached from before it existed.
        self.invalidate(name)

        text = getattr(raw, "prompt", template)
        if isinstance(text, list):
            text = "\n".join(
                str(m.get("content", "")) if isinstance(m, dict) else str(m)
                for m in text
            )
        return ManagedPrompt(
            name=name,
            template=str(text),
            version=str(getattr(raw, "version", "") or "") or None,
            labels=tuple(getattr(raw, "labels", ()) or tuple(labels)),
            config=dict(getattr(raw, "config", {}) or (config or {})),
            registered=True,
        )

    def invalidate(self, name: Optional[str] = None) -> None:
        """Drop cached prompts — all, or just one name."""
        with self._lock:
            if name is None:
                self._cache.clear()
            else:
                for key in [k for k in self._cache if k.startswith(f"{name}::")]:
                    del self._cache[key]


_registry: Optional[PromptRegistry] = None


def get_registry() -> PromptRegistry:
    """The process-wide prompt registry."""
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry


def get_prompt(name: str, *, version: Optional[str] = None,
               label: Optional[str] = "production",
               fallback: Optional[str] = None, **variables: Any) -> ManagedPrompt:
    """Fetch a managed prompt, optionally compiling it in one step.

        prompt = wt.get_prompt("support-assistant", fallback=LOCAL_DEFAULT)
        text = prompt.compile(company="Acme", vehicles=322)
    """
    return get_registry().get(name, version=version, label=label, fallback=fallback)


def create_prompt(name: str, template: Any, *,
                  labels: Sequence[str] = ("production",),
                  tags: Sequence[str] = (),
                  config: Optional[Dict[str, Any]] = None,
                  commit_message: Optional[str] = None) -> ManagedPrompt:
    """Publish a prompt version.

        wt.create_prompt(
            "support-assistant",
            "You are {{company}}'s support assistant. Cars: {{items}}.",
            labels=["production"],
            config={"model": "gemini-2.5-pro", "temperature": 0.2},
        )
    """
    return get_registry().create(
        name, template, labels=labels, tags=tags,
        config=config, commit_message=commit_message,
    )


#: How an agent's name becomes a registry key. An application with several
#: agents should not share one flat prompt namespace.
AGENT_PROMPT_PREFIX = "agent"

#: Separator between the prefix and the slug.
#:
#: Deliberately ``:`` and not ``/``. A slash reads better and Langfuse groups
#: on it in the UI, but the Langfuse client does not URL-encode the name when
#: it fetches — the path becomes ``/api/public/v2/prompts/agent/support`` and
#: the server answers 404. The write succeeds because the name travels in the
#: body, so a slash gives you a prompt you can publish and never read back.
AGENT_PROMPT_SEPARATOR = ":"


def agent_prompt_name(agent: Any) -> str:
    """The registry key for an agent: ``agent:<slug>``.

    Accepts the agent object, its ``slug``, or its display name, because call
    sites have different things to hand. Display names are slugified —
    ``"Support Assistant"`` and ``"support-assistant"`` must not resolve to
    two different prompts, or half the fleet silently runs an older version.
    """
    raw = (
        getattr(agent, "slug", None)
        or getattr(agent, "name", None)
        or str(agent or "")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", str(raw).strip().lower()).strip("-")
    return f"{AGENT_PROMPT_PREFIX}{AGENT_PROMPT_SEPARATOR}{slug}" if slug else AGENT_PROMPT_PREFIX


def get_agent_prompt(agent: Any, *, label: str = "production",
                     version: Optional[str] = None,
                     fallback: Optional[str] = None) -> ManagedPrompt:
    """The live prompt for one agent.

        prompt = wt.get_agent_prompt(agent, fallback=agent.system_prompt)
        system = prompt.compile(company=company.name, items=142)

    Pass the agent's own ``system_prompt`` as ``fallback`` and adoption is
    incremental: agents with a registry entry are managed from Langfuse,
    agents without one keep working off the database exactly as before, and
    ``prompt.registered`` tells the compliance report which is which.
    """
    return get_registry().get(
        agent_prompt_name(agent), version=version, label=label, fallback=fallback
    )
