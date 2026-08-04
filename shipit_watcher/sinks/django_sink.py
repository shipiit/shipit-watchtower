"""
Django sink — the local ledger.

Writes generations to ``agent.LLMCallRecord`` so the audit trail lives inside
the application's own database. Langfuse is where you *look* at traces; this is
where they are *kept*: retention outlives any hosted plan, the data never
leaves the host application's boundary, and cost-centre reporting is SQL rather than an export
from someone else's UI.

Only generations are persisted. Spans, decisions and retrievals are high-volume
and belong in the tracing backend; the ledger exists for cost, usage and
prompt-compliance reporting, and writing every span would make it slower at
exactly the queries it exists to answer.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..config import get_config
from ..context import TraceContext
from ..events import Event, EventType, GenerationEvent
from ..masking import mask_payload

logger = logging.getLogger(__name__)

__all__ = ["DjangoSink"]



def _as_pk(value):
    """Return *value* if it can be a UUID primary key, else None.

    Watcher deliberately accepts any string as a user or company id — an
    email, an SSO subject, a tenant slug. Django's UUID FK does not, and it
    rejects the *entire row* rather than the field, so an email-keyed trace
    used to vanish from the ledger with only a warning in the log.
    """
    if not value:
        return None
    try:
        import uuid

        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None
    return value

class DjangoSink:
    """Persists generations to the host application's database."""

    def start_trace(self, trace_id: str, name: str, context: TraceContext,
                    input_data: Any = None) -> None:
        # The ledger is a per-call table; a trace has no row of its own.
        return None

    def end_trace(self, trace_id: str, output: Any = None,
                  metadata: Optional[Dict[str, Any]] = None) -> None:
        return None

    def record(self, event: Event, context: TraceContext) -> None:
        # Generations always go to the cost ledger.
        if event.type == EventType.GENERATION:
            try:
                self._write(event, context)
            except Exception:
                # A failed write is a monitoring gap, never a failed request.
                logger.warning("watcher: could not persist LLM call", exc_info=True)

        # Optionally persist the whole tree so the decision path is
        # reconstructable locally, not only in the hosted UI.
        if get_config().persist_all_events:
            try:
                self._write_event(event, context)
            except Exception:
                logger.warning("watcher: could not persist trace event", exc_info=True)

    def _write_event(self, event: Event, context: TraceContext) -> None:
        """Persist one event as a node in the trace tree."""
        from datetime import datetime, timezone as dt_timezone

        from agent.models.trace_event import TraceEventRecord

        parent_event_id = event.parent_id or ""
        # Resolve the FK when the parent happens to be stored already. Children
        # normally finish *first* (an inner span completes before its parent),
        # so a missing row here is the common case, not an error —
        # ``parent_event_id`` still records the edge and ``stitch_parents``
        # can fill the FK in afterwards.
        parent_row = None
        if parent_event_id:
            parent_row = (
                TraceEventRecord.objects
                .filter(trace_id=context.trace_id or "", event_id=parent_event_id)
                .only("id", "depth")
                .first()
            )

        started_at = None
        if event.started_at:
            started_at = datetime.fromtimestamp(event.started_at, tz=dt_timezone.utc)

        TraceEventRecord.objects.create(
            trace_id=str(context.trace_id or "")[:64],
            event_id=str(event.id)[:64],
            parent_event_id=str(parent_event_id)[:64],
            parent=parent_row,
            # From the context: the parent row usually does not exist yet.
            depth=context.depth,
            name=str(event.name or "")[:255],
            event_type=event.type.value,
            severity=event.severity.value,
            status_message=mask_payload(event.status_message or ""),
            company_id=context.company_id or None,
            session_id=str(context.session_id or "")[:64],
            cost_center=str(context.cost_center or "")[:64],
            input=mask_payload(event.input),
            output=mask_payload(event.output),
            # to_payload() carries the type-specific fields: a decision's
            # rejected options, a retrieval's chunk hashes, a tool's name.
            attributes=mask_payload(event.to_payload()),
            duration_ms=event.duration_ms,
            started_at=started_at,
        )

    def _write(self, event: Event, context: TraceContext) -> None:
        from agent.models.llm_call import LLMCallRecord

        config = get_config()
        prompt = getattr(event, "prompt", {}) or {}

        metadata = dict(event.metadata)
        metadata.setdefault("service", config.service_name)
        metadata.setdefault("environment", config.environment)
        if event.duration_ms:
            metadata.setdefault("duration_ms", event.duration_ms)

        LLMCallRecord.objects.create(
            company_id=_as_pk(context.company_id),
            # The FK only when the value is a primary key; the raw identifier
            # always. Tracing by email is legitimate — and used — so it must
            # not cost the whole row.
            user_id=_as_pk(context.user_id),
            user_ref=str(context.user_id or "")[:255],
            session_id=str(context.session_id or "")[:64],
            trace_id=str(context.trace_id or "")[:64],
            observation_id=str(event.id or "")[:64],
            generation_name=str(event.name or "")[:128],
            prompt_name=str(prompt.get("prompt_name") or "")[:128],
            prompt_version=str(prompt.get("prompt_version") or "")[:32],
            prompt_fingerprint=str(prompt.get("prompt_fingerprint") or "")[:32],
            prompt_registered=bool(prompt.get("prompt_registered", False)),
            model=str(getattr(event, "model", "") or "")[:128],
            provider=str(getattr(event, "provider", "") or "")[:64],
            prompt_tokens=int(getattr(event, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(event, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(event, "total_tokens", 0) or 0),
            total_cost=getattr(event, "total_cost", 0) or 0,
            cost_center=str(context.cost_center or "")[:64],
            channel=str(context.channel or "")[:32],
            status=(
                LLMCallRecord.Status.ERROR
                if event.severity.value == "ERROR"
                else LLMCallRecord.Status.SUCCESS
            ),
            error_message=mask_payload(event.status_message or ""),
            latency_ms=event.duration_ms,
            # Masked again here: a sink must not assume an upstream layer
            # already did it, or turning off one call site silently starts
            # writing raw PII to disk.
            metadata=mask_payload(metadata),
        )

    @staticmethod
    def stitch_parents(trace_id: str) -> int:
        """Fill in parent FKs for a finished trace.

        Children are written before their parents, so the FK is usually null at
        insert time. ``parent_event_id`` always records the edge, and this
        resolves it into a real relation once the trace is complete — which is
        what lets the decision-path view walk ``children`` instead of
        re-deriving the tree on every read.

        Returns the number of rows linked.
        """
        from agent.models.trace_event import TraceEventRecord

        rows = list(TraceEventRecord.objects.filter(trace_id=trace_id))
        by_event_id = {r.event_id: r for r in rows}

        linked = []
        for row in rows:
            if row.parent_id is None and row.parent_event_id:
                parent = by_event_id.get(row.parent_event_id)
                if parent is not None:
                    row.parent = parent
                    linked.append(row)

        if linked:
            TraceEventRecord.objects.bulk_update(linked, ["parent"])
        return len(linked)

    def flush(self) -> None:
        return None
