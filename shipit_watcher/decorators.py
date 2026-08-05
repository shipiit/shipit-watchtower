"""
Decorators — the ergonomic surface.

Instrumentation only stays applied if adding it costs one line. Anything that
requires restructuring a function gets skipped under deadline, and partial
instrumentation is what produces orphan traces.

Every decorator here handles sync functions, async functions, generators and
async generators. Generators matter: an agent's streaming loop *is* a
generator, and a decorator that silently returns the generator object without
consuming it would record a span of ~0 ms and never see the real work or its
errors.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from .events import Event, EventType
from .tracer import get_tracer

__all__ = ["observe", "observe_agent", "observe_tool"]

F = TypeVar("F", bound=Callable[..., Any])


def _describe(func: Callable[..., Any], name: str | None) -> str:
    if name:
        return name
    module = getattr(func, "__module__", "") or ""
    qualname = getattr(func, "__qualname__", getattr(func, "__name__", "callable"))
    return f"{module.rsplit('.', 1)[-1]}.{qualname}" if module else qualname


def _capture_args(func, args, kwargs, capture: bool) -> Any:
    """Bind call arguments to parameter names for a readable trace input.

    ``self`` is dropped: it serialises to a repr nobody reads and can drag an
    entire ORM object into the payload.
    """
    if not capture:
        return None
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
    except Exception:
        return {"args": len(args), "kwargs": sorted(kwargs)}


def observe(
    name: str | None = None,
    *,
    event_type: EventType = EventType.SPAN,
    capture_input: bool = True,
    capture_output: bool = True,
    **metadata: Any,
) -> Callable[[F], F]:
    """Record a function as a span.

        @observe("fleet.summarise")
        async def summarise(company_id: str) -> str: ...

    Works on sync, async, generator and async-generator functions. Exceptions
    mark the span as errored and are re-raised unchanged.
    """

    def decorate(func: F) -> F:
        span_name = _describe(func, name)

        def _make_event() -> Event:
            return Event(name=span_name, type=event_type)

        if inspect.isasyncgenfunction(func):

            @functools.wraps(func)
            async def async_gen_wrapper(*args, **kwargs):
                tracer = get_tracer()
                payload = _capture_args(func, args, kwargs, capture_input)
                with tracer.span(span_name, event=_make_event(), input=payload,
                                 **metadata) as span:
                    count = 0
                    async for item in func(*args, **kwargs):
                        count += 1
                        yield item
                    # Streams have no single return value; the useful signal is
                    # how much was produced.
                    span.output = {"yielded": count} if capture_output else None

            return async_gen_wrapper  # type: ignore[return-value]

        if inspect.isgeneratorfunction(func):

            @functools.wraps(func)
            def gen_wrapper(*args, **kwargs):
                tracer = get_tracer()
                payload = _capture_args(func, args, kwargs, capture_input)
                with tracer.span(span_name, event=_make_event(), input=payload,
                                 **metadata) as span:
                    count = 0
                    for item in func(*args, **kwargs):
                        count += 1
                        yield item
                    span.output = {"yielded": count} if capture_output else None

            return gen_wrapper  # type: ignore[return-value]

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                tracer = get_tracer()
                payload = _capture_args(func, args, kwargs, capture_input)
                with tracer.span(span_name, event=_make_event(), input=payload,
                                 **metadata) as span:
                    result = await func(*args, **kwargs)
                    if capture_output:
                        span.output = result
                    return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = get_tracer()
            payload = _capture_args(func, args, kwargs, capture_input)
            with tracer.span(span_name, event=_make_event(), input=payload, **metadata) as span:
                result = func(*args, **kwargs)
                if capture_output:
                    span.output = result
                return result

        return sync_wrapper  # type: ignore[return-value]

    return decorate


def observe_tool(tool_name: str | None = None, **metadata: Any) -> Callable[[F], F]:
    """Record a function as a tool invocation.

    Distinct from :func:`observe` because tool calls are counted, costed and
    shown as their own step in a decision path — a generic span would be
    invisible to all three.
    """

    def decorate(func: F) -> F:
        name = tool_name or getattr(func, "NAME", None) or func.__name__

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                tracer = get_tracer()
                payload = _capture_args(func, args, kwargs, True)
                with tracer.tool(name, arguments=payload, **metadata) as event:
                    result = await func(*args, **kwargs)
                    event.output = result
                    return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = get_tracer()
            payload = _capture_args(func, args, kwargs, True)
            with tracer.tool(name, arguments=payload, **metadata) as event:
                result = func(*args, **kwargs)
                event.output = result
                return result

        return sync_wrapper  # type: ignore[return-value]

    return decorate


def observe_agent(agent_name: str | None = None, **metadata: Any) -> Callable[[F], F]:
    """Open a root trace for an agent's whole turn.

    Use at the outermost entry point. Everything below attaches automatically,
    which is what stops nested calls appearing as unparented top-level traces.
    """

    def decorate(func: F) -> F:
        name = agent_name or func.__name__

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                tracer = get_tracer()
                payload = _capture_args(func, args, kwargs, True)
                with tracer.trace(f"agent.{name}", input=payload, **metadata):
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = get_tracer()
            payload = _capture_args(func, args, kwargs, True)
            with tracer.trace(f"agent.{name}", input=payload, **metadata):
                return func(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]

    return decorate
