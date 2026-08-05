"""
Langfuse sink.

Owns trace creation explicitly rather than leaving it to LiteLLM's
auto-callback. That distinction is the fix for the duplicate-trace problem: if
both this SDK *and* ``litellm.success_callback = ["langfuse"]`` are active, the
same call is logged twice under different names — once as ``chat.session`` with
full metadata, once as a flat ``litellm-completion`` with ``{}``. See
``shipit_watcher.instrumentation.litellm``.

Supports both the v2 (``langfuse.trace()``) and v3 (``start_span()``) client
shapes, because Langfuse ships breaking changes often and the host app's pinned
version is not ours to dictate.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_config
from ..context import TraceContext
from ..events import Event, EventType, GenerationEvent

logger = logging.getLogger(__name__)

__all__ = ["LangfuseSink"]


class LangfuseSink:
    """Ships events to Langfuse, tolerating SDK major-version differences."""

    def __init__(self, client: Any = None):
        self._client = client or self._build_client()
        # trace_id -> the SDK's trace handle.
        self._traces: dict[str, Any] = {}
        # event_id -> that observation's handle. Langfuse nests by calling
        # .span()/.generation() on the PARENT handle, not on the trace — using
        # the trace for everything is what produced a flat list instead of a
        # tree.
        self._observations: dict[str, Any] = {}
        # trace_id -> events awaiting an ordered flush.
        self._pending: dict[str, list] = {}

    # -- construction ---------------------------------------------------

    @staticmethod
    def _build_client() -> Any:
        config = get_config()
        if not config.has_langfuse_credentials:
            return None
        try:
            from langfuse import Langfuse

            return Langfuse(
                public_key=config.langfuse_public_key,
                secret_key=config.langfuse_secret_key,
                host=config.langfuse_host,
            )
        except Exception:
            logger.warning("watcher: could not construct Langfuse client", exc_info=True)
            return None

    @property
    def available(self) -> bool:
        return self._client is not None

    # -- lifecycle ------------------------------------------------------

    def start_trace(self, trace_id: str, name: str, context: TraceContext,
                    input_data: Any = None) -> None:
        if not self.available:
            return
        config = get_config()

        # Langfuse v3 has a first-class environment; v2 does not, so it travels
        # in metadata *and* as an `env:` tag to keep filtering working on both.
        metadata = context.merged_metadata({
            "service": config.service_name,
            "environment": config.environment,
            "release": config.release,
            "cost_center": context.cost_center,
            "company_id": context.company_id,
            "channel": context.channel,
        })
        tags = context.merged_tags([
            f"service:{config.service_name}",
            f"env:{config.environment}",
            *( [f"company:{context.company_id}"] if context.company_id else [] ),
            *( [f"cost_center:{context.cost_center}"] if context.cost_center else [] ),
        ])

        try:
            # v2 style — the version the host application pins today.
            trace = self._client.trace(
                id=trace_id,
                name=name,
                user_id=context.user_id,
                session_id=context.session_id,
                metadata=metadata,
                tags=tags,
                input=input_data,
            )
            self._traces[trace_id] = trace
        except AttributeError:
            # v3 removed .trace() in favour of spans; a root span is equivalent.
            try:
                span = self._client.start_span(name=name, input=input_data, metadata=metadata)
                self._traces[trace_id] = span
            except Exception:
                logger.warning("watcher: langfuse start_trace failed", exc_info=True)
        except Exception:
            logger.warning("watcher: langfuse start_trace failed", exc_info=True)

    def end_trace(self, trace_id: str, output: Any = None,
                  metadata: dict[str, Any] | None = None) -> None:
        # Emit buffered observations first — they attach to this trace.
        self._flush_pending(trace_id)

        trace = self._traces.pop(trace_id, None)
        # Drop the handles or the map grows for the life of the process.
        self._observations.clear()
        if trace is None:
            return
        try:
            if hasattr(trace, "update"):
                trace.update(output=output, metadata=metadata or {})
            elif hasattr(trace, "end"):
                trace.end(output=output)
        except Exception:
            logger.warning("watcher: langfuse end_trace failed", exc_info=True)

    # -- events ---------------------------------------------------------

    def record(self, event: Event, context: TraceContext) -> None:
        """Buffer the event; it is emitted when the trace closes.

        Langfuse builds the tree by creating a child *on its parent's handle*,
        so parents must exist first. But spans complete inner-to-outer, so
        events arrive child-first. Emitting on arrival therefore produced a
        flat list — every observation parented to the trace.

        Buffering and flushing in parent-before-child order is what makes the
        graph render. The cost is that observations appear when the trace
        closes rather than mid-request, which for a request-scoped trace is
        imperceptible.
        """
        if not self.available:
            return
        trace_id = context.trace_id or ""
        if not trace_id:
            return
        self._pending.setdefault(trace_id, []).append(event)

    def _flush_pending(self, trace_id: str) -> None:
        """Emit one trace's events, parents before children."""
        events = self._pending.pop(trace_id, [])
        if not events:
            return

        by_id = {e.id: e for e in events}

        def depth_of(event: Event) -> int:
            # Walk to the root, guarding against a cycle from a malformed
            # parent_id rather than looping forever on the request path.
            depth, seen, node = 0, {event.id}, event
            while node.parent_id and node.parent_id in by_id:
                node = by_id[node.parent_id]
                if node.id in seen:
                    break
                seen.add(node.id)
                depth += 1
            return depth

        for event in sorted(events, key=lambda e: (depth_of(e), e.started_at)):
            try:
                parent = self._observations.get(event.parent_id or "")
                target = parent or self._traces.get(trace_id)
                payload = event.to_payload()
                if isinstance(event, GenerationEvent) or event.type == EventType.GENERATION:
                    self._record_generation(target, event, None, payload)
                else:
                    self._record_span(target, event, None, payload)
            except Exception:
                logger.warning("watcher: langfuse record failed", exc_info=True)

    def _record_generation(self, trace, event, context, payload) -> None:
        usage = {
            "input": getattr(event, "prompt_tokens", 0),
            "output": getattr(event, "completion_tokens", 0),
            "total": getattr(event, "total_tokens", 0),
        }
        kwargs = dict(
            name=event.name,
            model=getattr(event, "model", ""),
            input=event.input,
            output=event.output,
            metadata=payload,
            usage=usage,
            level=event.severity.value,
            status_message=event.status_message or None,
        )
        target = trace if trace is not None else self._client
        handle = None
        if hasattr(target, "generation"):
            handle = target.generation(**kwargs)
        elif hasattr(target, "start_generation"):
            handle = target.start_generation(**kwargs)
            if hasattr(handle, "end"):
                handle.end()
        if handle is not None:
            self._observations[event.id] = handle

    def _record_span(self, trace, event, context, payload) -> None:
        kwargs = dict(
            name=event.name,
            input=event.input,
            output=event.output,
            metadata=payload,
            level=event.severity.value,
            status_message=event.status_message or None,
        )
        target = trace if trace is not None else self._client
        span = None
        if hasattr(target, "span"):
            span = target.span(**kwargs)
        elif hasattr(target, "start_span"):
            span = target.start_span(**kwargs)
        if span is not None:
            # Registered BEFORE end() so children created later still resolve.
            self._observations[event.id] = span
            if hasattr(span, "end"):
                span.end()

    def record_score(self, score: Any) -> None:
        """Attach a score to its trace or observation.

        Scores are how quality joins cost and latency — without them a trace
        says what happened but never whether it was any good.
        """
        if not self.available:
            return
        try:
            payload = {
                "name": score.name,
                "value": score.numeric_value
                if score.numeric_value is not None
                else score.value,
                "comment": score.comment or None,
                "trace_id": score.trace_id,
            }
            if score.observation_id:
                payload["observation_id"] = score.observation_id
            self._client.score(**{k: v for k, v in payload.items() if v is not None})
        except Exception:
            logger.warning("watcher: langfuse score failed", exc_info=True)

    def flush(self) -> None:
        if not self.available:
            return
        try:
            self._client.flush()
        except Exception:
            logger.warning("watcher: langfuse flush failed", exc_info=True)
