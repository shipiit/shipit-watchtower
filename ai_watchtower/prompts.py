"""
Prompt registry — fetch managed prompts from Langfuse, with a safety net.

This is the other half of :mod:`ai_watchtower.identity`. Identity answers
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
from typing import Any, Dict, Optional

from .config import get_config
from .identity import PromptIdentity, fingerprint_text

logger = logging.getLogger(__name__)

__all__ = ["ManagedPrompt", "PromptRegistry", "get_registry", "get_prompt"]

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
            logger.warning("watchtower: prompt registry client unavailable", exc_info=True)
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
            logger.warning("watchtower: serving stale prompt %r", name)
            return ManagedPrompt(
                name=entry.prompt.name, template=entry.prompt.template,
                version=entry.prompt.version, labels=entry.prompt.labels,
                config=entry.prompt.config, registered=entry.prompt.registered,
                stale=True,
            )

        logger.warning("watchtower: prompt %r unresolved, using fallback", name)
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
        except Exception:
            logger.warning("watchtower: could not fetch prompt %r", name, exc_info=True)
            return None

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

        prompt = wt.get_prompt("fleet-assistant", fallback=LOCAL_DEFAULT)
        text = prompt.compile(company="Acme", vehicles=322)
    """
    return get_registry().get(name, version=version, label=label, fallback=fallback)
