"""
OpenAI SDK instrumentation.

The OpenAI client is the most common way a Python application talks to a
model, and until now Watcher could only see it if the call happened to go
through LiteLLM. That made "connect Watcher to my project" a refactor rather
than a line of setup, which is the wrong trade: instrumentation people have to
restructure code for is instrumentation that gets skipped.

This patches the SDK's own request methods, once, at the class level — so it
covers every client instance, including ones created inside libraries you do
not control. Anything the SDK routes through ``chat.completions.create``,
``responses.create`` or ``embeddings.create``, sync or async, streaming or
not, reports into the ambient Watcher trace.

Two things this does that a naive wrapper does not:

* **Streaming stays open.** The span closes when the stream is exhausted, not
  when the method returns, so the recorded duration is the real one and the
  final usage chunk is seen. Time-to-first-token is captured separately,
  because for a streamed answer that is the latency a user actually feels.
* **Cost is computed when nobody reports it.** OpenAI returns usage, never
  price. Without :mod:`shipit_watcher.pricing` every OpenAI call would show
  ``$0.00`` in a cost report.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

from ..context import current_context
from ..events import GenerationEvent
from ..pricing import estimate_cost
from ..tracer import get_tracer
from ._stream_proxy import TracedStream, fail_event, record_event

logger = logging.getLogger(__name__)

__all__ = ["instrument", "is_instrumented", "uninstrument"]

_instrumented = False
_originals: list[tuple[Any, str, Any]] = []

#: Operation names, so a trace says what the system was doing rather than
#: which method transported it.
_OPERATION = {
    "chat.completions": "llm.completion",
    "responses": "llm.response",
    "embeddings": "rag.embedding",
}


def _usage_from(payload: Any) -> tuple[int, int, int]:
    """(prompt, completion, cached) tokens, across both API shapes."""
    usage = getattr(payload, "usage", None)
    if usage is None:
        return 0, 0, 0

    def field(*names: str) -> int:
        for name in names:
            value = (
                usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            )
            if value:
                return int(value)
        return 0

    # The Responses API renamed prompt/completion to input/output.
    prompt = field("prompt_tokens", "input_tokens")
    completion = field("completion_tokens", "output_tokens")

    details = (
        usage.get("prompt_tokens_details") if isinstance(usage, dict)
        else getattr(usage, "prompt_tokens_details", None)
    ) or (
        usage.get("input_tokens_details") if isinstance(usage, dict)
        else getattr(usage, "input_tokens_details", None)
    )
    cached = 0
    if details is not None:
        cached = int(
            (details.get("cached_tokens") if isinstance(details, dict)
             else getattr(details, "cached_tokens", 0)) or 0
        )
    return prompt, completion, cached


def _text_from(payload: Any) -> Any:
    """The answer, whichever API produced it."""
    output_text = getattr(payload, "output_text", None)
    if output_text:
        return output_text
    choices = getattr(payload, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content:
            return content
        calls = getattr(message, "tool_calls", None)
        if calls:
            return {"tool_calls": [
                {"name": getattr(getattr(c, "function", None), "name", ""),
                 "arguments": getattr(getattr(c, "function", None), "arguments", "")}
                for c in calls
            ]}
    return None


def _start_event(operation: str, kwargs: dict[str, Any]) -> GenerationEvent:
    context = current_context()
    event = GenerationEvent(
        name=_OPERATION.get(operation, f"llm.{operation}"),
        model=str(kwargs.get("model", "") or ""),
        provider="openai",
        parent_id=context.parent_id,
    )
    event.input = kwargs.get("messages") or kwargs.get("input")
    ambient = context.prompt
    if ambient:
        event.prompt = dict(ambient)
    for name in ("temperature", "max_tokens", "max_output_tokens", "top_p"):
        if kwargs.get(name) is not None:
            event.metadata[name] = kwargs[name]
    return event


def _finish(event: GenerationEvent, payload: Any, started: float) -> None:
    prompt_tokens, completion_tokens, cached = _usage_from(payload)
    event.prompt_tokens = prompt_tokens
    event.completion_tokens = completion_tokens
    # OpenAI reports usage, never price.
    event.total_cost = estimate_cost(event.model, prompt_tokens, completion_tokens, cached)
    if cached:
        event.metadata["cached_tokens"] = cached
    event.output = _text_from(payload)
    event.metadata.setdefault("latency_ms", int((time.time() - started) * 1000))
    event.finish()


def _delta_text(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None)
    if choices:
        delta = getattr(choices[0], "delta", None)
        return getattr(delta, "content", None) or "" if delta else ""
    # Responses API streams typed events rather than choices.
    return getattr(chunk, "delta", "") if isinstance(getattr(chunk, "delta", None), str) else ""


def _wrap(original: Any, operation: str, *, is_async: bool):
    if getattr(original, "_watcher_wrapped", False):
        return original

    if is_async:
        @functools.wraps(original)
        async def wrapper(self, *args, **kwargs):
            tracer = get_tracer()
            if not tracer.active:
                return await original(self, *args, **kwargs)
            event = _start_event(operation, kwargs)
            started = time.time()
            streaming = bool(kwargs.get("stream"))
            if streaming:
                kwargs.setdefault("stream_options", {"include_usage": True})
            try:
                result = await original(self, *args, **kwargs)
            except BaseException as exc:
                fail_event(event, exc)
                raise
            if streaming:
                return TracedStream(result, event, started,
                                    usage_of=_usage_from, text_of=_delta_text)
            _finish(event, result, started)
            record_event(event)
            return result
    else:
        @functools.wraps(original)
        def wrapper(self, *args, **kwargs):
            tracer = get_tracer()
            if not tracer.active:
                return original(self, *args, **kwargs)
            event = _start_event(operation, kwargs)
            started = time.time()
            streaming = bool(kwargs.get("stream"))
            if streaming:
                kwargs.setdefault("stream_options", {"include_usage": True})
            try:
                result = original(self, *args, **kwargs)
            except BaseException as exc:
                fail_event(event, exc)
                raise
            if streaming:
                return TracedStream(result, event, started,
                                    usage_of=_usage_from, text_of=_delta_text)
            _finish(event, result, started)
            record_event(event)
            return result

    setattr(wrapper, "_watcher_wrapped", True)  # noqa: B010
    return wrapper


#: (module path, class name, operation) for every entry point worth patching.
#: Patched on the class, not the client, so an instance constructed inside a
#: library nobody owns is covered too.
_TARGETS = (
    ("openai.resources.chat.completions", "Completions", "chat.completions", False),
    ("openai.resources.chat.completions", "AsyncCompletions", "chat.completions", True),
    ("openai.resources.responses", "Responses", "responses", False),
    ("openai.resources.responses", "AsyncResponses", "responses", True),
    ("openai.resources.embeddings", "Embeddings", "embeddings", False),
    ("openai.resources.embeddings", "AsyncEmbeddings", "embeddings", True),
)


def instrument() -> bool:
    """Trace every OpenAI SDK call. Idempotent; returns whether it is active."""
    global _instrumented
    if _instrumented:
        return True
    try:
        import openai  # noqa: F401
    except ImportError:
        logger.debug("watcher: openai not installed; skipping instrumentation")
        return False

    import importlib

    patched = 0
    for module_path, class_name, operation, is_async in _TARGETS:
        try:
            module = importlib.import_module(module_path)
            target = getattr(module, class_name, None)
            if target is None:
                continue
            original = target.create
            if getattr(original, "_watcher_wrapped", False):
                continue
            _originals.append((target, "create", original))
            target.create = _wrap(original, operation, is_async=is_async)
            patched += 1
        except Exception:
            # A surface this SDK does not have in this version is not an error.
            logger.debug("watcher: could not patch %s.%s", module_path, class_name,
                         exc_info=True)

    _instrumented = patched > 0
    if _instrumented:
        logger.info("watcher: openai instrumented (%d entry points)", patched)
    return _instrumented


def uninstrument() -> None:
    """Restore the SDK's own methods."""
    global _instrumented
    for target, name, original in _originals:
        try:
            setattr(target, name, original)
        except Exception:
            logger.debug("watcher: could not restore %s", name, exc_info=True)
    _originals.clear()
    _instrumented = False


def is_instrumented() -> bool:
    return _instrumented
