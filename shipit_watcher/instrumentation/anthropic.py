"""
Anthropic SDK instrumentation.

Same contract as the OpenAI integration: patch the SDK's own methods once, at
the class level, so every client instance is covered — including ones created
inside a library you do not control — and every call reports into the ambient
Watcher trace with usage, cost and prompt identity attached.

Anthropic's shapes differ in three ways worth naming, because each one is a
place a generic wrapper reports the wrong number:

* usage is ``input_tokens`` / ``output_tokens``, not prompt/completion;
* prompt caching is billed separately and reported as
  ``cache_read_input_tokens`` and ``cache_creation_input_tokens`` — counting
  those as ordinary input overstates the cost of a cached call several-fold;
* ``messages.stream()`` returns a manager rather than an iterator, so the
  usual "wrap the returned iterable" trick misses it entirely.
"""

from __future__ import annotations

import functools
import importlib
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


def _usage_from(payload: Any) -> tuple[int, int, int]:
    """(input, output, cached) tokens.

    Cached reads are returned separately so pricing can bill them at the
    cached rate — roughly a tenth of the input rate on current models.
    """
    usage = getattr(payload, "usage", None)
    # A streamed message carries usage on the event's nested message.
    if usage is None:
        usage = getattr(getattr(payload, "message", None), "usage", None)
    if usage is None:
        return 0, 0, 0

    def field(name: str) -> int:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return int(value or 0)

    cached = field("cache_read_input_tokens")
    # Cache *creation* is billed at a premium rather than a discount, so it
    # counts as ordinary input rather than as a cached read.
    written = field("cache_creation_input_tokens")
    # Anthropic reports `input_tokens` *excluding* cache reads, while OpenAI
    # includes them in `prompt_tokens`. Watcher records one meaning — total
    # input, cached portion called out separately — so a token count means
    # the same thing whichever provider produced it, and pricing can subtract
    # the cached share without knowing where the numbers came from.
    return field("input_tokens") + written + cached, field("output_tokens"), cached


def _text_from(payload: Any) -> Any:
    blocks = getattr(payload, "content", None)
    if not blocks:
        return None
    text = "".join(
        getattr(block, "text", "") or "" for block in blocks
        if getattr(block, "type", "") == "text"
    )
    tools = [
        {"name": getattr(block, "name", ""), "input": getattr(block, "input", None)}
        for block in blocks if getattr(block, "type", "") == "tool_use"
    ]
    if text and tools:
        return {"text": text, "tool_calls": tools}
    return text or ({"tool_calls": tools} if tools else None)


def _delta_text(chunk: Any) -> str:
    delta = getattr(chunk, "delta", None)
    return getattr(delta, "text", "") or "" if delta is not None else ""


def _start_event(kwargs: dict[str, Any]) -> GenerationEvent:
    context = current_context()
    event = GenerationEvent(
        name="llm.completion",
        model=str(kwargs.get("model", "") or ""),
        provider="anthropic",
        parent_id=context.parent_id,
    )
    # The system prompt is part of what produced the answer, so it belongs in
    # the recorded input rather than being dropped as a keyword argument.
    system = kwargs.get("system")
    messages = kwargs.get("messages")
    event.input = ({"system": system, "messages": messages} if system else messages)
    ambient = context.prompt
    if ambient:
        event.prompt = dict(ambient)
    for name in ("temperature", "max_tokens", "top_p", "top_k"):
        if kwargs.get(name) is not None:
            event.metadata[name] = kwargs[name]
    return event


def _finish(event: GenerationEvent, payload: Any, started: float) -> None:
    input_tokens, output_tokens, cached = _usage_from(payload)
    event.prompt_tokens = input_tokens
    event.completion_tokens = output_tokens
    event.total_cost = estimate_cost(event.model, input_tokens, output_tokens, cached)
    if cached:
        event.metadata["cached_tokens"] = cached
    event.output = _text_from(payload)
    event.metadata.setdefault("latency_ms", int((time.time() - started) * 1000))
    stop = getattr(payload, "stop_reason", None)
    if stop:
        event.metadata["stop_reason"] = stop
    event.finish()


def _traced(result: Any, event: GenerationEvent, started: float) -> TracedStream:
    return TracedStream(result, event, started, usage_of=_usage_from, text_of=_delta_text)


def _wrap_create(original: Any, *, is_async: bool):
    if getattr(original, "_watcher_wrapped", False):
        return original

    if is_async:
        @functools.wraps(original)
        async def wrapper(self, *args, **kwargs):
            if not get_tracer().active:
                return await original(self, *args, **kwargs)
            event = _start_event(kwargs)
            started = time.time()
            try:
                result = await original(self, *args, **kwargs)
            except BaseException as exc:
                fail_event(event, exc)
                raise
            if kwargs.get("stream"):
                return _traced(result, event, started)
            _finish(event, result, started)
            record_event(event)
            return result
    else:
        @functools.wraps(original)
        def wrapper(self, *args, **kwargs):
            if not get_tracer().active:
                return original(self, *args, **kwargs)
            event = _start_event(kwargs)
            started = time.time()
            try:
                result = original(self, *args, **kwargs)
            except BaseException as exc:
                fail_event(event, exc)
                raise
            if kwargs.get("stream"):
                return _traced(result, event, started)
            _finish(event, result, started)
            record_event(event)
            return result

    setattr(wrapper, "_watcher_wrapped", True)  # noqa: B010
    return wrapper


def _wrap_stream(original: Any):
    """``messages.stream()`` returns a manager, not an iterable.

    Wrapping it like ``create(stream=True)`` would hand the caller something
    that is not a manager and break ``with client.messages.stream(...) as s``.
    So the manager is left intact and its ``__enter__``/``__aenter__`` result
    is what gets proxied.
    """
    if getattr(original, "_watcher_wrapped", False):
        return original

    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        manager = original(self, *args, **kwargs)
        if not get_tracer().active:
            return manager
        event = _start_event(kwargs)
        started = time.time()
        return _ManagerProxy(manager, event, started)

    setattr(wrapper, "_watcher_wrapped", True)  # noqa: B010
    return wrapper


class _ManagerProxy:
    """Forwards everything, but traces whatever the manager yields."""

    def __init__(self, manager: Any, event: GenerationEvent, started: float):
        self._manager = manager
        self._event = event
        self._started = started
        self._traced: TracedStream | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)

    def __enter__(self):
        self._traced = _traced(self._manager.__enter__(), self._event, self._started)
        return self._traced

    def __exit__(self, *exc_info):
        if self._traced is not None:
            self._traced._close()
        return self._manager.__exit__(*exc_info)

    async def __aenter__(self):
        self._traced = _traced(
            await self._manager.__aenter__(), self._event, self._started
        )
        return self._traced

    async def __aexit__(self, *exc_info):
        if self._traced is not None:
            self._traced._close()
        return await self._manager.__aexit__(*exc_info)


_TARGETS = (
    ("anthropic.resources.messages", "Messages", False),
    ("anthropic.resources.messages", "AsyncMessages", True),
    # Older SDKs kept these one level up.
    ("anthropic.resources", "Messages", False),
    ("anthropic.resources", "AsyncMessages", True),
)


def instrument() -> bool:
    """Trace every Anthropic SDK call. Idempotent."""
    global _instrumented
    if _instrumented:
        return True
    try:
        import anthropic  # noqa: F401
    except ImportError:
        logger.debug("watcher: anthropic not installed; skipping instrumentation")
        return False

    patched = 0
    for module_path, class_name, is_async in _TARGETS:
        try:
            module = importlib.import_module(module_path)
            target = getattr(module, class_name, None)
            if target is None:
                continue
            for attribute, wrap in (("create", _wrap_create), ("stream", None)):
                original = getattr(target, attribute, None)
                if original is None or getattr(original, "_watcher_wrapped", False):
                    continue
                _originals.append((target, attribute, original))
                setattr(
                    target, attribute,
                    _wrap_create(original, is_async=is_async) if wrap
                    else _wrap_stream(original),
                )
                patched += 1
        except Exception:
            logger.debug("watcher: could not patch %s.%s", module_path, class_name,
                         exc_info=True)

    _instrumented = patched > 0
    if _instrumented:
        logger.info("watcher: anthropic instrumented (%d entry points)", patched)
    return _instrumented


def uninstrument() -> None:
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
