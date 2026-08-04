"""
Ambient trace context.

The alternative to this module is threading ``trace_id`` and
``parent_observation_id`` through every function that might emit an event —
which is how instrumentation ends up half-applied: the moment one call site
forgets, its spans detach and reappear as orphan top-level traces. That is
precisely the failure visible in FleetFlow today, where LiteLLM generations
show up flat, unparented, with empty metadata.

``contextvars`` fixes it properly: the context follows the logical flow of
execution, across ``await`` boundaries and into ``asyncio`` tasks, without
being global state shared between concurrent requests. Each request gets its
own view; a thread pool inherits a copy rather than racing.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterator, List, Optional

__all__ = ["TraceContext", "current_context", "bind", "get_trace_id", "get_parent_id"]


@dataclass(frozen=True)
class TraceContext:
    """Who and what the current unit of work belongs to.

    Frozen: nesting produces a *new* context rather than mutating the caller's,
    so an inner span can never corrupt its parent's view on the way back out.
    """

    trace_id: Optional[str] = None
    parent_id: Optional[str] = None
    #: Nesting level. Carried on the context because an inner span completes
    #: before its parent is written, so depth cannot be derived from stored
    #: rows at write time.
    depth: int = 0

    # ── Business dimensions, carried onto every event ────────────────
    user_id: Optional[str] = None
    company_id: Optional[str] = None
    session_id: Optional[str] = None
    #: MPK / cost centre. The RFP requires allocation at gateway level, which
    #: means it has to be in scope wherever a call is made — not looked up
    #: afterwards from something that may since have changed.
    cost_center: Optional[str] = None
    channel: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def child(self, parent_id: str) -> "TraceContext":
        """Context for work nested under ``parent_id``."""
        return replace(self, parent_id=parent_id)

    def merged_metadata(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        combined = dict(self.metadata)
        if extra:
            combined.update(extra)
        return combined

    def merged_tags(self, extra: Optional[List[str]] = None) -> List[str]:
        if not extra:
            return list(self.tags)
        # dict.fromkeys preserves order while removing duplicates.
        return list(dict.fromkeys([*self.tags, *extra]))


_context: ContextVar[TraceContext] = ContextVar(
    "ai_watchtower_context", default=TraceContext()
)


def current_context() -> TraceContext:
    """The context in effect right now. Never ``None``."""
    return _context.get()


def get_trace_id() -> Optional[str]:
    return current_context().trace_id


def get_parent_id() -> Optional[str]:
    return current_context().parent_id


@contextmanager
def bind(**fields: Any) -> Iterator[TraceContext]:
    """Bind values onto the ambient context for the duration of the block.

        with bind(company_id=str(company.id), cost_center="fleet-ops"):
            ...                       # every event here carries both

    ``tags`` and ``metadata`` merge with what is already bound; everything else
    replaces. Restoration uses the token from ``set()``, so it is correct even
    when blocks are nested or an exception unwinds several at once.
    """
    base = _context.get()

    tags = fields.pop("tags", None)
    metadata = fields.pop("metadata", None)

    known = {k: v for k, v in fields.items() if hasattr(base, k)}
    updated = replace(
        base,
        **known,
        tags=base.merged_tags(tags),
        metadata=base.merged_metadata(metadata),
    )

    token: Token = _context.set(updated)
    try:
        yield updated
    finally:
        _context.reset(token)
