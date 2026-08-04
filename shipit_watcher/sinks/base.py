"""
Sinks — where events go.

A ``Sink`` is the only thing that knows about a backend. The tracer builds
typed events and hands them over; Langfuse, the local database and a console
logger are then interchangeable, and adding ClickHouse later is a new file
rather than a change to the call sites.

Two rules every sink must honour:

1. **Never raise.** A sink failure is swallowed by :class:`FanOutSink`, but a
   sink that raises inside its own buffering can still lose a whole batch.
2. **Never block meaningfully.** Sinks sit on the request path. Anything slow
   belongs behind the sink's own queue.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Protocol, runtime_checkable

from ..context import TraceContext
from ..events import Event

logger = logging.getLogger(__name__)

__all__ = ["Sink", "FanOutSink", "ConsoleSink"]


@runtime_checkable
class Sink(Protocol):
    """The contract a backend adapter implements."""

    def start_trace(self, trace_id: str, name: str, context: TraceContext,
                    input_data: Any = None) -> None:
        ...

    def end_trace(self, trace_id: str, output: Any = None,
                  metadata: Optional[Dict[str, Any]] = None) -> None:
        ...

    def record(self, event: Event, context: TraceContext) -> None:
        """Persist one completed event."""
        ...

    def flush(self) -> None:
        ...


class FanOutSink:
    """Broadcasts to several sinks, isolating each from the others' failures.

    This is what makes "Langfuse *and* the local ledger" safe: if Langfuse is
    unreachable, the database row is still written, and vice versa. A sink that
    raises is logged once per event and skipped — never retried in-line, since
    that would put a failing backend on the user's critical path.
    """

    def __init__(self, sinks: Iterable[Sink]):
        self._sinks: List[Sink] = [s for s in sinks if s is not None]

    def __bool__(self) -> bool:
        return bool(self._sinks)

    def _each(self, operation: str, *args, **kwargs) -> None:
        for sink in self._sinks:
            try:
                getattr(sink, operation)(*args, **kwargs)
            except Exception:
                logger.warning(
                    "watcher: sink %s failed during %s",
                    type(sink).__name__, operation, exc_info=True,
                )

    def start_trace(self, trace_id: str, name: str, context: TraceContext,
                    input_data: Any = None) -> None:
        self._each("start_trace", trace_id, name, context, input_data)

    def end_trace(self, trace_id: str, output: Any = None,
                  metadata: Optional[Dict[str, Any]] = None) -> None:
        self._each("end_trace", trace_id, output, metadata)

    def record(self, event: Event, context: TraceContext) -> None:
        self._each("record", event, context)

    def flush(self) -> None:
        self._each("flush")


class ConsoleSink:
    """Human-readable output for local development.

    Deliberately terse: one line per event, indented by depth, so a decision
    path is legible in a terminal without opening a UI.
    """

    def __init__(self, logger_name: str = "shipit_watcher.console"):
        self._log = logging.getLogger(logger_name)

    def start_trace(self, trace_id: str, name: str, context: TraceContext,
                    input_data: Any = None) -> None:
        self._log.info("┌ trace %s · %s · company=%s", trace_id[:8], name,
                       context.company_id or "-")

    def end_trace(self, trace_id: str, output: Any = None,
                  metadata: Optional[Dict[str, Any]] = None) -> None:
        self._log.info("└ trace %s complete", trace_id[:8])

    def record(self, event: Event, context: TraceContext) -> None:
        marker = "✗" if event.severity.value == "ERROR" else "•"
        self._log.info(
            "  %s %-16s %-28s %5dms", marker, event.type.value, event.name,
            event.duration_ms,
        )

    def flush(self) -> None:
        return None
