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

import inspect
import logging
import time
from typing import Any, Dict, Optional

from ..context import TraceContext, current_context
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
    """Best-effort cost. LiteLLM reports it in several places by version.

    ``_hidden_params["response_cost"]`` is checked first and matters most:
    behind a proxy the model is an alias the local pricing map has never heard
    of, so ``completion_cost`` computes 0 and the proxy's own figure — which
    lands here — is the only real number available.
    """
    hidden = getattr(response, "_hidden_params", None)
    if not isinstance(hidden, dict):
        hidden = (kwargs.get("litellm_params") or {}).get("_hidden_params") or {}
    for candidate in (
        hidden.get("response_cost") if isinstance(hidden, dict) else None,
        (kwargs.get("standard_logging_object") or {}).get("response_cost")
        if isinstance(kwargs, dict) else None,
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
    return _computed_cost(kwargs, response)


def _computed_cost(kwargs: Dict[str, Any], response: Any) -> float:
    """Price the call ourselves when nobody reported a figure.

    Streaming calls through a gateway routinely arrive with every cost field
    at zero — the response is reassembled from chunks and the proxy's own
    number never makes it back. Reporting $0 then is worse than reporting
    nothing: it makes an expensive model look free in the ledger.
    """
    try:
        import litellm
    except Exception:
        return 0.0

    # The routing prefix has to go first. Pricing knows `gemini-2.5-flash`;
    # `openai/gemini-2.5-flash` is an instruction to litellm about *how* to
    # reach it and matches no pricing entry.
    model = str(kwargs.get("model", "") or "").split("/")[-1]

    # Only price a model the table actually knows. Both litellm helpers fall
    # back to a generic rate for anything unrecognised, which would quietly
    # invent a cost for a private deployment — worse than reporting none,
    # because an invented number looks authoritative.
    if not model or model not in (getattr(litellm, "model_cost", None) or {}):
        return 0.0

    prompt_tokens, completion_tokens = _usage_from(response)
    if not (prompt_tokens or completion_tokens):
        return 0.0

    try:
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return float(prompt_cost + completion_cost)
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
    # LiteLLM relocates metadata into litellm_params on some paths, so both
    # are checked — a name the caller chose should not depend on which one.
    for holder in (kwargs.get("metadata"),
                   (kwargs.get("litellm_params") or {}).get("metadata")):
        if isinstance(holder, dict) and holder.get("generation_name"):
            return str(holder["generation_name"])
    call_type = str(kwargs.get("call_type", "") or "completion")
    return _CALL_NAMES.get(call_type, f"llm.{call_type}")


#: Key under which the calling thread's trace context travels with the request.
#:
#: LiteLLM runs the success handler for a *streaming* call on a plain
#: ``threading.Thread``, and a plain thread does not inherit ``contextvars``.
#: So by the time the handler asks "which trace am I in?", the answer is
#: "none" — and the generation detaches from the turn that made it. That is
#: how a fully wired agent trace ends up showing tool spans and no LLM call.
#:
#: The fix is to read the context in the caller's thread, where it is still
#: correct, and send it along with the request. LiteLLM passes ``metadata``
#: through to the callback untouched, so it is a reliable envelope.
_CONTEXT_KEY = "_watcher_context"

#: The litellm entry points wrapped to stamp the context.
_WRAPPED_FUNCTIONS = ("completion", "acompletion", "embedding", "aembedding",
                      "text_completion", "atext_completion")

_originals: Dict[str, Any] = {}


def _snapshot_context() -> Dict[str, Any]:
    """The ambient context as a plain dict, safe to hand to another thread."""
    context = current_context()
    return {
        "trace_id": context.trace_id,
        "parent_id": context.parent_id,
        "user_id": context.user_id,
        "company_id": context.company_id,
        "session_id": context.session_id,
        "cost_center": context.cost_center,
        "channel": context.channel,
        "tags": list(context.tags),
        "metadata": dict(context.metadata),
        "prompt": dict(context.prompt),
    }


def _restore_context(payload: Any) -> Optional[TraceContext]:
    if not isinstance(payload, dict) or not payload.get("trace_id"):
        return None
    fields = TraceContext.__dataclass_fields__
    return TraceContext(**{k: v for k, v in payload.items() if k in fields})


#: Keys the LiteLLM **proxy** reads from a request's metadata to decide where
#: its own Langfuse trace goes. A gateway with server-side Langfuse logging
#: cannot be silenced by a client — but it can be told which trace to join.
#: Without these it opens an unparented ``litellm-acompletion`` beside every
#: real trace, with no session and no user.
_PROXY_TRACE_KEYS = ("existing_trace_id", "generation_name", "session_id",
                     "trace_user_id")

#: Prompt identity travels under the names the registry already uses, so a
#: gateway enforcing prompt governance needs no translation table.
_PROMPT_KEYS = ("prompt_name", "prompt_version", "prompt_fingerprint",
                "prompt_registered")


def _governance_payload(context: TraceContext) -> Dict[str, Any]:
    """Attribution and prompt identity under the gateway's wire names.

    Everything comes from the ambient context or the configuration, never from
    a lookup: a cost centre resolved after the fact may since have changed, and
    an attribution that is only *usually* right is not one you can bill from.
    """
    from ..config import get_config

    config = get_config()
    if not getattr(config, "gateway_attribution", False):
        return {}

    sources = {
        "service_name": config.service_name,
        "environment": config.environment,
        "cost_center": context.cost_center,
        "company_id": context.company_id,
        "user_id": context.user_id,
        "session_id": context.session_id,
        "channel": context.channel,
    }

    payload: Dict[str, Any] = {}
    for wire_name, source_name in (getattr(config, "gateway_key_map", None) or {}).items():
        value = sources.get(source_name)
        if value not in (None, ""):
            payload[str(wire_name)] = str(value)

    # Prompt identity is forwarded verbatim. A gateway in enforce mode decides
    # on `prompt_name`/`prompt_version`; renaming them would defeat it.
    prompt = context.prompt or {}
    for key in _PROMPT_KEYS:
        value = prompt.get(key)
        if value not in (None, ""):
            payload[key] = value if key == "prompt_registered" else str(value)
    return payload


def _generation_owner() -> str:
    """``app`` (this SDK writes generations) or ``gateway`` (the proxy does)."""
    from ..config import get_config

    value = str(getattr(get_config(), "generation_owner", "app") or "app").strip().lower()
    return "gateway" if value == "gateway" else "app"


def _stamp(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the calling thread's context to this request's metadata.

    Two audiences, one envelope. ``_CONTEXT_KEY`` is for our own callback,
    which may run on another thread. The Langfuse-shaped keys are for a
    LiteLLM proxy that logs server-side: it reads them and attaches its
    generation to this trace instead of opening its own.
    """
    context = current_context()
    if context.trace_id is None:
        return kwargs

    metadata = dict(kwargs.get("metadata") or {})
    metadata.setdefault(_CONTEXT_KEY, _snapshot_context())

    # setdefault throughout: a caller that named its own generation, or
    # deliberately pointed at a different trace, is not overridden.
    metadata.setdefault("existing_trace_id", context.trace_id)
    metadata.setdefault("generation_name", _call_name(kwargs))
    if context.session_id:
        metadata.setdefault("session_id", context.session_id)
    if context.user_id:
        metadata.setdefault("trace_user_id", context.user_id)

    kwargs["metadata"] = metadata

    # ...and again in extra_body, which is the only route to a *proxy*.
    # `metadata` is not an OpenAI field, and litellm is normally run with
    # drop_params=True, so it is stripped from the request body before it
    # leaves. extra_body is forwarded verbatim, so a gateway logging
    # server-side can still see which trace to join.
    extra_body = dict(kwargs.get("extra_body") or {})
    forwarded = dict(extra_body.get("metadata") or {})
    for key in _PROXY_TRACE_KEYS:
        if key in metadata:
            forwarded.setdefault(key, metadata[key])

    # Attribution and prompt identity ride the same envelope. A gateway that
    # allocates cost or enforces prompt governance reads them here; one that
    # does not simply ignores unknown keys.
    for key, value in _governance_payload(context).items():
        forwarded.setdefault(key, value)

    if forwarded:
        extra_body["metadata"] = forwarded
        kwargs["extra_body"] = extra_body

    return kwargs


def _wrap(func):
    """Wrap a litellm entry point so it carries the context to the callback."""
    import functools

    if getattr(func, "_watcher_wrapped", False):
        return func

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **_stamp(kwargs))
    else:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **_stamp(kwargs))

    wrapper._watcher_wrapped = True
    return wrapper


def _context_from(kwargs: Dict[str, Any]) -> TraceContext:
    """The context this call was made in — stamped, or ambient as a fallback.

    Ambient is checked second rather than first: on the non-streaming path the
    callback runs inline and the two agree, but on the streaming path only the
    stamp is trustworthy.
    """
    for holder in (kwargs.get("metadata"),
                   (kwargs.get("litellm_params") or {}).get("metadata")):
        if isinstance(holder, dict):
            restored = _restore_context(holder.get(_CONTEXT_KEY))
            if restored is not None:
                return restored
    return current_context()


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
            kwargs = kwargs if isinstance(kwargs, dict) else {}
            context = _context_from(kwargs)
            tracer = get_tracer()

            # When the proxy owns the generation record, stay out of its way:
            # it logs server-side into the same trace (we hand it
            # `existing_trace_id`), so emitting here would duplicate the call —
            # the very thing this module prevents, one layer further out.
            # Failures are still worth recording: a gateway that rejected the
            # request never logged a generation for it.
            if _generation_owner() == "gateway" and error is None:
                return

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



def _handler_class():
    """Build the handler class, subclassing LiteLLM's ``CustomLogger``.

    LiteLLM dispatches ``litellm.callbacks`` with
    ``isinstance(callback, CustomLogger)``, so a duck-typed handler is
    silently ignored — registered, never called, no error anywhere. The
    subclass is created here rather than at import time because this package
    must stay importable without LiteLLM installed.
    """
    try:
        from litellm.integrations.custom_logger import CustomLogger
    except Exception:
        return WatcherLiteLLMHandler

    class _Handler(WatcherLiteLLMHandler, CustomLogger):
        pass

    _Handler.__name__ = "WatcherLiteLLMHandler"
    _Handler.__qualname__ = "WatcherLiteLLMHandler"
    return _Handler


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

        # Wrap the entry points so each call carries its context. Without
        # this, streaming generations detach from their turn.
        for name in _WRAPPED_FUNCTIONS:
            original = getattr(litellm, name, None)
            if original is not None and not getattr(original, "_watcher_wrapped", False):
                _originals[name] = original
                setattr(litellm, name, _wrap(original))

        handler_class = _handler_class()
        handler = handler_class()
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
        for name, original in _originals.items():
            setattr(litellm, name, original)
        _originals.clear()

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
