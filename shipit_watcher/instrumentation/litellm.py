"""
LiteLLM instrumentation.

Fixes the duplicate-trace problem directly. Today the host application sets::

    litellm.success_callback = ["langfuse"]

which makes LiteLLM open its **own** Langfuse trace per call. When the
application also creates a trace, the same call is logged twice under
different names — ``litellm-completion`` and ``litellm-acompletion`` at the
identical timestamp, with identical tokens and cost, and ``{}`` for metadata —
and neither carries the tool spans, because those belong to the application's
trace. Add a proxy-side callback and it becomes three.

The rule this module enforces: **exactly one component owns tracing.** Calling
:func:`instrument` removes LiteLLM's Langfuse callback and installs a handler
that reports into the current watcher trace instead. One trace per request,
correctly parented, carrying tenant, cost centre and prompt identity.

If you would rather keep LiteLLM's native callback, do not call
``instrument()`` — but then do not create application traces either, or the
duplicates return.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from ..context import current_context
from ..events import GenerationEvent, Severity
from ..tracer import get_tracer

logger = logging.getLogger(__name__)

__all__ = ["instrument", "uninstrument", "is_instrumented"]

_instrumented = False
_saved_callbacks: Dict[str, Any] = {}


def _usage_from(response: Any) -> tuple[int, int]:
    """Pull (prompt, completion) tokens from a response of unknown shape."""
    usage = getattr(response, "usage", None) or {}
    if isinstance(usage, dict):
        return int(usage.get("prompt_tokens", 0) or 0), int(
            usage.get("completion_tokens", 0) or 0
        )
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


def _cost_from(kwargs: Dict[str, Any], response: Any) -> float:
    """Best-effort cost. LiteLLM reports it in several places by version."""
    for candidate in (
        (kwargs.get("response_cost") if isinstance(kwargs, dict) else None),
        (kwargs.get("litellm_params", {}) or {}).get("response_cost")
        if isinstance(kwargs, dict) else None,
        getattr(response, "_response_cost", None),
    ):
        if candidate:
            try:
                return float(candidate)
            except (TypeError, ValueError):
                continue
    try:
        import litellm

        return float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception:
        return 0.0


#: LiteLLM's own call-type names, translated into operation names. The point
#: of the whole module: `litellm-aembedding` says how the bytes travelled;
#: `rag.embedding` says what the system was doing.
_CALL_NAMES = {
    "embedding": "rag.embedding",
    "aembedding": "rag.embedding",
    "completion": "llm.completion",
    "acompletion": "llm.completion",
    "text_completion": "llm.completion",
    "atext_completion": "llm.completion",
    "image_generation": "llm.image",
    "transcription": "llm.transcription",
    "moderation": "llm.moderation",
}


def _call_name(kwargs: Dict[str, Any]) -> str:
    """What to call this call.

    An explicit `metadata["generation_name"]` wins — a caller that named the
    operation knows more than we can infer.
    """
    explicit = (kwargs.get("metadata") or {}).get("generation_name")
    if explicit:
        return str(explicit)
    call_type = str(kwargs.get("call_type", "") or "completion")
    return _CALL_NAMES.get(call_type, f"llm.{call_type}")


class WatcherLiteLLMHandler:
    """LiteLLM ``CustomLogger`` that reports into the ambient watcher trace.

    LiteLLM instantiates and calls this itself, on both the sync and async
    paths, so every method is defensive: a raise here would surface inside the
    user's completion call.
    """

    def log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        self._record(kwargs, response_obj, start_time, end_time, error=None)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time) -> None:
        self._record(kwargs, response_obj, start_time, end_time,
                     error=kwargs.get("exception") if isinstance(kwargs, dict) else None)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        self._record(kwargs, response_obj, start_time, end_time, error=None)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time) -> None:
        self._record(kwargs, response_obj, start_time, end_time,
                     error=kwargs.get("exception") if isinstance(kwargs, dict) else None)

    # -- internals ------------------------------------------------------

    def _record(self, kwargs, response_obj, start_time, end_time, error) -> None:
        try:
            context = current_context()
            tracer = get_tracer()
            kwargs = kwargs if isinstance(kwargs, dict) else {}

            prompt_tokens, completion_tokens = _usage_from(response_obj)
            model = str(kwargs.get("model", "") or "")
            name = _call_name(kwargs)

            if context.trace_id is None:
                # No ambient trace — a background job: reindexing, a nightly
                # report. Dropping these used to seem right, but it means
                # turning instrumentation on makes work *disappear* from
                # Langfuse. Give it a trace of its own instead, named after
                # what it is: `rag.embedding`, never `litellm-aembedding`.
                self._record_standalone(tracer, name, model, prompt_tokens,
                                        completion_tokens, kwargs, response_obj,
                                        start_time, end_time, error)
                return

            event = GenerationEvent(
                name=name,
                model=model,
                provider=str(
                    (kwargs.get("litellm_params", {}) or {}).get("custom_llm_provider", "")
                ),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_cost=_cost_from(kwargs, response_obj),
                parent_id=context.parent_id,
            )

            # Prompt identity: explicit per-call metadata wins, then whatever
            # `wt.use_prompt(...)` bound around this call. The ambient fallback
            # is what makes attribution complete — a `litellm.completion` deep
            # inside a tool has no way to pass metadata, and without it that
            # call is indistinguishable from an unregistered prompt.
            prompt_meta = (kwargs.get("metadata") or {}).get("watcher_prompt")
            if not isinstance(prompt_meta, dict):
                prompt_meta = context.prompt
            if isinstance(prompt_meta, dict) and prompt_meta:
                event.prompt = dict(prompt_meta)

            event.started_at = _as_epoch(start_time) or time.time()
            event.ended_at = _as_epoch(end_time) or time.time()

            if error is not None:
                event.severity = Severity.ERROR
                event.status_message = str(error)[:500]

            tracer._emit(event, context)  # noqa: SLF001 — internal by design
        except Exception:
            logger.warning("watcher: litellm handler failed", exc_info=True)


    def _record_standalone(self, tracer, name, model, prompt_tokens,
                           completion_tokens, kwargs, response_obj,
                           start_time, end_time, error) -> None:
        """Trace a call made outside any request, as its own named trace."""
        with tracer.trace(name, channel="background") as context:
            event = GenerationEvent(
                name=name,
                model=model,
                provider=str(
                    (kwargs.get("litellm_params", {}) or {}).get("custom_llm_provider", "")
                ),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_cost=_cost_from(kwargs, response_obj),
                parent_id=context.parent_id,
            )
            event.started_at = _as_epoch(start_time) or time.time()
            event.ended_at = _as_epoch(end_time) or time.time()
            if error is not None:
                event.severity = Severity.ERROR
                event.status_message = str(error)[:500]
            tracer._emit(event, context)  # noqa: SLF001


def _as_epoch(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        try:
            return float(timestamp())
        except Exception:
            return None
    return None


def instrument(*, replace_langfuse_callback: bool = True) -> bool:
    """Install the handler and take ownership of LLM-call tracing.

    Returns True when instrumentation is active. Idempotent.

    ``replace_langfuse_callback`` removes LiteLLM's built-in ``"langfuse"``
    callback, which is what stops each call being traced twice. Set it False
    only if you intend to keep LiteLLM's own traces and are not creating
    application traces.
    """
    global _instrumented
    if _instrumented:
        return True

    try:
        import litellm
    except ImportError:
        logger.info("watcher: litellm not installed; skipping instrumentation")
        return False

    try:
        _saved_callbacks["success"] = list(getattr(litellm, "success_callback", []) or [])
        _saved_callbacks["failure"] = list(getattr(litellm, "failure_callback", []) or [])

        if replace_langfuse_callback:
            litellm.success_callback = [
                c for c in _saved_callbacks["success"] if c != "langfuse"
            ]
            litellm.failure_callback = [
                c for c in _saved_callbacks["failure"] if c != "langfuse"
            ]

        handler = WatcherLiteLLMHandler()
        callbacks = list(getattr(litellm, "callbacks", []) or [])
        if not any(isinstance(c, WatcherLiteLLMHandler) for c in callbacks):
            callbacks.append(handler)
        litellm.callbacks = callbacks

        _instrumented = True
        logger.info(
            "watcher: litellm instrumented (langfuse callback %s)",
            "removed" if replace_langfuse_callback else "kept",
        )
        return True
    except Exception:
        logger.warning("watcher: litellm instrumentation failed", exc_info=True)
        return False


def uninstrument() -> None:
    """Restore LiteLLM's callbacks to their pre-``instrument`` state."""
    global _instrumented
    if not _instrumented:
        return
    try:
        import litellm

        litellm.callbacks = [
            c for c in (getattr(litellm, "callbacks", []) or [])
            if not isinstance(c, WatcherLiteLLMHandler)
        ]
        if "success" in _saved_callbacks:
            litellm.success_callback = _saved_callbacks["success"]
        if "failure" in _saved_callbacks:
            litellm.failure_callback = _saved_callbacks["failure"]
    except Exception:
        logger.warning("watcher: litellm uninstrument failed", exc_info=True)
    finally:
        _instrumented = False


def is_instrumented() -> bool:
    return _instrumented
