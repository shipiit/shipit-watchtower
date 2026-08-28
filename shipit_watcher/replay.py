"""Portable trace bundles for replay and structural comparison."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import TraceContext
from .events import Event

__all__ = [
    "TraceBundle",
    "TraceBundleSink",
    "compare_bundles",
    "load_bundle",
    "replay_bundle",
]


@dataclass
class TraceBundle:
    schema_version: str = "watcher.trace.v1"
    project: str = ""
    trace_id: str = ""
    name: str = ""
    input: Any = None
    output: Any = None
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project": self.project,
            "trace_id": self.trace_id,
            "name": self.name,
            "input": self.input,
            "output": self.output,
            "context": self.context,
            "metadata": self.metadata,
            "events": self.events,
        }


class TraceBundleSink:
    """Capture sanitised Watcher events and optionally persist them as JSON.

    With a ``path``, each finished trace is written to that file and dropped
    from memory — so the file holds the **most recent** trace, which is what
    the single-trace debugging workflow wants. Point the sink at a new path
    per trace to keep more than one.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self.bundles: dict[str, TraceBundle] = {}

    def start_trace(
        self,
        trace_id: str,
        name: str,
        context: TraceContext,
        input_data: Any = None,
    ) -> None:
        from .config import get_config

        self.bundles[trace_id] = TraceBundle(
            project=get_config().project,
            trace_id=trace_id,
            name=name,
            input=input_data,
            context={
                "user_id": context.user_id,
                "session_id": context.session_id,
                "company_id": context.company_id,
                "cost_center": context.cost_center,
                "channel": context.channel,
                "tags": context.tags,
                "metadata": context.metadata,
            },
        )

    def record(self, event: Event, context: TraceContext) -> None:
        bundle = self.bundles.get(context.trace_id or "")
        if bundle is not None:
            bundle.events.append({
                "id": event.id,
                "parent_id": event.parent_id,
                "name": event.name,
                "event_type": event.type.value,
                "started_at": event.started_at,
                "ended_at": event.ended_at,
                "severity": event.severity.value,
                "status_message": event.status_message,
                "input": event.input,
                "output": event.output,
                **event.to_payload(),
            })

    def end_trace(
        self,
        trace_id: str,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        bundle = self.bundles.get(trace_id)
        if bundle is None:
            return
        bundle.output = output
        bundle.metadata = metadata or {}
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(bundle.to_dict(), indent=2, default=str), encoding="utf-8"
            )
            # Written, so nothing here needs it again. A file-backed sink left
            # on a long-lived tracer would otherwise retain every trace it ever
            # saw — a debugging aid that quietly becomes a leak. The in-memory
            # form (path=None) keeps its bundles: reading them back *is* the
            # API there.
            self.bundles.pop(trace_id, None)

    def flush(self) -> None:
        return None


def load_bundle(path: str | Path) -> TraceBundle:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "watcher.trace.v1":
        raise ValueError("unsupported Watcher trace bundle schema")
    return TraceBundle(**payload)


def replay_bundle(bundle: TraceBundle, task: Callable[[Any], Any]) -> Any:
    """Run a bundle's original input through a replacement task/model."""
    return task(bundle.input)


def compare_bundles(before: TraceBundle, after: TraceBundle) -> dict[str, Any]:
    """Return a compact structural diff suitable for CI or debugging."""
    before_types = [event.get("event_type") or event.get("type") for event in before.events]
    after_types = [event.get("event_type") or event.get("type") for event in after.events]

    def total(field: str, bundle: TraceBundle) -> float:
        return sum(float(event.get(field) or 0) for event in bundle.events)

    return {
        "output_changed": before.output != after.output,
        "event_types_changed": before_types != after_types,
        "before_event_types": before_types,
        "after_event_types": after_types,
        "cost_delta": total("total_cost", after) - total("total_cost", before),
        "token_delta": total("total_tokens", after) - total("total_tokens", before),
    }
