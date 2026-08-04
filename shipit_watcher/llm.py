"""
The LLM gateway — one call path, fully observed.

Instrumentation that sits *beside* the call has to be remembered. This is the
call, so it cannot be forgotten: every completion made through :class:`LLMClient`
is traced, costed, attributed to a tenant and a cost centre, tied to a prompt
version, and PII-masked, because there is no way to make one that is not.

That matters more than it sounds. the host application's agent loop and the SDK both
opened traces for the same turn, producing two records of one event — the exact
duplication this package exists to remove. A single gateway settles it: the
application asks for a completion and observability is a property of the
answer, not a second thing to wire up.

What it adds over calling ``litellm.completion`` directly:

* a generation span with usage, cost and prompt identity, nested in the
  ambient trace;
* streaming that still reports accurate token counts and latency — a naive
  wrapper returns the generator and records a 0 ms span that never sees the
  output;
* governance: in ``enforce`` mode an unregistered prompt is refused before the
  call is made, which is the only place a block can be applied cheaply;
* provider-agnostic retries that do not retry things which will never succeed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .config import get_config
from .context import current_context
from .events import Severity
from .identity import PromptIdentity
from .tracer import get_tracer

logger = logging.getLogger(__name__)

__all__ = ["LLMClient", "LLMResponse", "GovernanceError",
           "complete", "stream", "run_prompt"]


class GovernanceError(RuntimeError):
    """Raised when policy refuses a call — e.g. an unregistered prompt in
    ``enforce`` mode. Deliberately not a subclass of any provider error: it is
    our decision, not the model's."""


@dataclass
class LLMResponse:
    """A completion, plus everything worth recording about how it was produced."""

    text: str = ""
    model: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost: float = 0.0
    latency_ms: int = 0
    finish_reason: str = ""
    #: The provider's raw response, for callers that need tool_calls etc.
    raw: Any = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __str__(self) -> str:
        return self.text


def _extract_usage(response: Any) -> tuple[int, int]:
    """Token counts from a response whose shape varies by provider and version."""
    usage = getattr(response, "usage", None) or {}
    if isinstance(usage, dict):
        return (
            int(usage.get("prompt_tokens", 0) or 0),
            int(usage.get("completion_tokens", 0) or 0),
        )
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


def _extract_cost(response: Any) -> float:
    """Cost, preferring what the provider reported over what we can compute.

    ``_hidden_params["response_cost"]`` is checked first and matters most:
    behind a LiteLLM proxy the model is an alias the local pricing map has
    never heard of, so ``completion_cost`` returns 0 and the proxy's own
    figure — which lands here — is the only real number available.
    """
    hidden = getattr(response, "_hidden_params", None) or {}
    candidates = [
        hidden.get("response_cost") if isinstance(hidden, dict) else None,
        getattr(response, "_response_cost", None),
    ]
    for candidate in candidates:
        if candidate:
            try:
                return float(candidate)
            except (TypeError, ValueError):
                continue
    try:
        import litellm

        return float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception:
        # Cost is nice to have; never let its absence fail a completion.
        return 0.0


def _extract_text(response: Any) -> tuple[str, str, List[Dict[str, Any]]]:
    """(text, finish_reason, tool_calls) from a completion response."""
    try:
        choice = response.choices[0]
        message = getattr(choice, "message", None)
        text = getattr(message, "content", None) or ""
        finish = getattr(choice, "finish_reason", "") or ""
        raw_calls = getattr(message, "tool_calls", None) or []
        calls = [
            {
                "id": getattr(c, "id", ""),
                "name": getattr(getattr(c, "function", None), "name", ""),
                "arguments": getattr(getattr(c, "function", None), "arguments", ""),
            }
            for c in raw_calls
        ]
        return text, finish, calls
    except (AttributeError, IndexError):
        return "", "", []


#: Errors where an immediate retry is pointless — a bad request stays bad, and
#: an auth failure will not fix itself between attempts.
_NON_RETRYABLE = ("authenticationerror", "badrequesterror", "notfounderror",
                  "invalidrequesterror", "contentpolicyviolation")


def _is_retryable(exc: Exception) -> bool:
    return type(exc).__name__.lower() not in _NON_RETRYABLE


class LLMClient:
    """A traced, governed LiteLLM client.

    ::

        client = wt.LLMClient(model="gemini-2.5-pro")
        answer = client.complete(
            messages=[{"role": "user", "content": "How many items?"}],
            prompt=prompt_identity,
        )

    Tenant, user and cost centre come from the ambient trace context, so they
    do not have to be passed at every call site.
    """

    def __init__(
        self,
        model: str,
        *,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        max_retries: int = 2,
        fallback_models: Sequence[str] = (),
    ):
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.provider = provider
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        #: Tried in order if the primary model keeps failing. Each attempt is
        #: recorded, so a fallback never happens invisibly.
        self.fallback_models = tuple(fallback_models)

    # -- governance -----------------------------------------------------

    def _check_governance(self, prompt: Optional[PromptIdentity]) -> None:
        """Apply the configured governance level before spending money.

        ``audit`` records and allows. ``warn`` additionally emits a policy
        event. ``enforce`` refuses. The refusal happens *pre-call* because
        that is the only point where blocking is free.
        """
        mode = (get_config().governance_mode or "audit").lower()
        if mode == "audit":
            return

        registered = bool(prompt and prompt.registered)
        if registered:
            return

        tracer = get_tracer()
        name = prompt.name if prompt else "<none>"
        if mode == "enforce":
            tracer.policy("prompt_registry", blocked=True,
                          reason=f"prompt {name!r} is not registered")
            raise GovernanceError(
                f"Prompt {name!r} is not in the registry and governance mode is "
                f"'enforce'. Register it, or set WATCHER_GOVERNANCE=warn."
            )
        tracer.policy("prompt_registry", blocked=False,
                      reason=f"prompt {name!r} is not registered")

    # -- request building -----------------------------------------------

    def _route(self, model: str) -> str:
        """Resolve a bare model name against ``api_base``.

        A gateway alias like ``gemini-2.5-pro`` is not the upstream model — it
        is a name the proxy resolves. Passed unprefixed, litellm recognises the
        family and routes natively to Vertex, ignoring ``api_base`` entirely
        and failing on credentials the caller never intended to use. The
        ``openai/`` prefix says "treat this as an OpenAI-compatible endpoint",
        which is what a proxy is.

        An explicit ``provider/model`` is left alone — the caller has said
        where it goes.
        """
        if not self.api_base or "/" in model:
            return model
        return f"{self.provider}/{model}" if self.provider else f"openai/{model}"

    def _build_kwargs(self, messages, model, overrides: Dict[str, Any]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self._route(model),
            "messages": messages,
            "temperature": overrides.pop("temperature", self.temperature),
            "timeout": overrides.pop("timeout", self.timeout),
        }
        max_tokens = overrides.pop("max_tokens", self.max_tokens)
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.provider:
            kwargs["custom_llm_provider"] = self.provider
        kwargs.update(overrides)
        return kwargs

    def _models_to_try(self) -> List[str]:
        return [self.model, *self.fallback_models]

    # -- completion -----------------------------------------------------

    def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        prompt: Optional[PromptIdentity] = None,
        name: str = "llm.completion",
        **overrides: Any,
    ) -> LLMResponse:
        """Run a completion, fully traced.

        Raises :class:`GovernanceError` before calling the provider when policy
        refuses; otherwise re-raises the provider's own exception once every
        model and retry is exhausted.
        """
        import litellm

        self._check_governance(prompt)
        tracer = get_tracer()
        last_error: Optional[Exception] = None

        for model in self._models_to_try():
            for attempt in range(self.max_retries + 1):
                started = time.time()
                with tracer.generation(
                    name, model=model, provider=self.provider or "",
                    prompt=prompt, input=messages,
                    attempt=attempt + 1,
                    is_fallback=model != self.model,
                ) as generation:
                    try:
                        raw = litellm.completion(
                            **self._build_kwargs(messages, model, dict(overrides))
                        )
                    except Exception as exc:  # provider error
                        last_error = exc
                        generation.severity = Severity.ERROR
                        generation.status_message = f"{type(exc).__name__}: {exc}"[:500]
                        if not _is_retryable(exc):
                            # A bad request or bad key stays bad — move to the
                            # next model rather than burning retries.
                            break
                        continue

                    text, finish, tool_calls = _extract_text(raw)
                    prompt_tokens, completion_tokens = _extract_usage(raw)

                    generation.prompt_tokens = prompt_tokens
                    generation.completion_tokens = completion_tokens
                    generation.total_cost = _extract_cost(raw)
                    generation.output = text or {"tool_calls": tool_calls}

                    return LLMResponse(
                        text=text, model=model, provider=self.provider or "",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_cost=generation.total_cost,
                        latency_ms=int((time.time() - started) * 1000),
                        finish_reason=finish, raw=raw, tool_calls=tool_calls,
                    )

        raise last_error or RuntimeError("LLM call failed with no exception recorded")

    # -- streaming ------------------------------------------------------

    def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        prompt: Optional[PromptIdentity] = None,
        name: str = "llm.stream",
        **overrides: Any,
    ) -> Iterator[str]:
        """Stream text deltas, recording accurate usage when the stream ends.

        The span stays open for the whole stream. Returning the generator and
        closing the span immediately — the obvious implementation — records
        0 ms, no tokens, and never sees an error raised mid-stream.

        ``stream_options.include_usage`` is requested so the final chunk
        carries real token counts instead of an estimate.
        """
        import litellm

        self._check_governance(prompt)
        tracer = get_tracer()

        kwargs = self._build_kwargs(messages, self.model, dict(overrides))
        kwargs["stream"] = True
        kwargs.setdefault("stream_options", {"include_usage": True})

        started = time.time()
        with tracer.generation(
            name, model=self.model, provider=self.provider or "",
            prompt=prompt, input=messages, streaming=True,
        ) as generation:
            collected: List[str] = []
            prompt_tokens = completion_tokens = 0

            for chunk in litellm.completion(**kwargs):
                usage = getattr(chunk, "usage", None)
                if usage:
                    prompt_tokens, completion_tokens = _extract_usage(chunk)

                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                piece = getattr(delta, "content", None) if delta else None
                if piece:
                    collected.append(piece)
                    yield piece

            text = "".join(collected)
            generation.prompt_tokens = prompt_tokens
            generation.completion_tokens = completion_tokens
            generation.output = text
            generation.metadata["latency_ms"] = int((time.time() - started) * 1000)
            generation.metadata["chunks"] = len(collected)


# ── Module-level convenience ─────────────────────────────────────────────

def complete(model: str, messages: List[Dict[str, Any]], **kwargs: Any) -> LLMResponse:
    """One-shot completion without constructing a client."""
    client_kwargs = {
        k: kwargs.pop(k)
        for k in ("api_base", "api_key", "provider", "temperature",
                  "max_tokens", "timeout", "max_retries", "fallback_models")
        if k in kwargs
    }
    return LLMClient(model, **client_kwargs).complete(messages, **kwargs)


def stream(model: str, messages: List[Dict[str, Any]], **kwargs: Any) -> Iterator[str]:
    """One-shot streaming completion without constructing a client."""
    client_kwargs = {
        k: kwargs.pop(k)
        for k in ("api_base", "api_key", "provider", "temperature",
                  "max_tokens", "timeout", "max_retries", "fallback_models")
        if k in kwargs
    }
    return LLMClient(model, **client_kwargs).stream(messages, **kwargs)


# ── The one-call path ────────────────────────────────────────────────────

def run_prompt(
    name: str,
    *,
    variables: Optional[Dict[str, Any]] = None,
    user_message: str = "",
    messages: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    label: str = "production",
    version: Optional[str] = None,
    fallback: Optional[str] = None,
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    generation_name: Optional[str] = None,
    **overrides: Any,
) -> LLMResponse:
    """Fetch the live prompt, run it, and record everything about the run.

    The whole loop an agent turn actually needs, in one call::

        answer = wt.run_prompt(
            "support-assistant",
            variables={"company": "Acme", "vehicles": 142},
            user_message="How many items do we have?",
        )

    What it does that a hand-written version usually forgets:

    * resolves the version currently labelled ``production`` — so a prompt
      change ships without a deploy, and rolls back the same way;
    * takes ``model``, ``temperature`` and ``max_tokens`` from the prompt's
      own ``config``, so tuning is a registry change too;
    * links the generation to that exact version, which is what makes
      "why this answer" answerable later;
    * degrades through the same ladder as :func:`get_prompt` — a registry
      outage serves the last good prompt rather than failing the turn.

    Credentials default to the ``LITELLM_API_BASE`` / ``LITELLM_API_KEY``
    environment, so the common case passes neither.
    """
    import os

    from .prompts import get_prompt

    prompt = get_prompt(name, version=version, label=label, fallback=fallback)
    config = prompt.config or {}

    if messages is None:
        system = prompt.compile(**(variables or {}))
        messages = [{"role": "system", "content": system}]
        if user_message:
            messages.append({"role": "user", "content": user_message})

    client = LLMClient(
        model=model or config.get("model") or "gpt-4.1-mini",
        api_base=api_base or os.getenv("LITELLM_API_BASE"),
        api_key=api_key or os.getenv("LITELLM_API_KEY"),
        temperature=overrides.pop("temperature", config.get("temperature", 0.7)),
        max_tokens=overrides.pop("max_tokens", config.get("max_tokens")),
        fallback_models=tuple(config.get("fallback_models", ())),
    )
    return client.complete(
        messages,
        prompt=prompt.identity,
        name=generation_name or f"llm.{name}",
        **overrides,
    )
