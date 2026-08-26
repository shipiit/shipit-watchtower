"""
Ambient trace context.

The alternative to this module is threading ``trace_id`` and
``parent_observation_id`` through every function that might emit an event —
which is how instrumentation ends up half-applied: the moment one call site
forgets, its spans detach and reappear as orphan top-level traces. That is
precisely the failure visible in the host application today, where LiteLLM generations
show up flat, unparented, with empty metadata.

``contextvars`` fixes it properly: the context follows the logical flow of
execution, across ``await`` boundaries and into ``asyncio`` tasks, without
being global state shared between concurrent requests. Each request gets its
own view; a thread pool inherits a copy rather than racing.
"""

from __future__ import annotations

import logging

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "TraceContext",
    "bind",
    "current_context",
    "get_parent_id",
    "get_trace_id",
    "use_prompt",
]


@dataclass(frozen=True)
class TraceContext:
    """Who and what the current unit of work belongs to.

    Frozen: nesting produces a *new* context rather than mutating the caller's,
    so an inner span can never corrupt its parent's view on the way back out.
    """

    trace_id: str | None = None
    parent_id: str | None = None
    #: Nesting level. Carried on the context because an inner span completes
    #: before its parent is written, so depth cannot be derived from stored
    #: rows at write time.
    depth: int = 0

    # ── Business dimensions, carried onto every event ────────────────
    user_id: str | None = None
    company_id: str | None = None
    session_id: str | None = None
    #: cost centre. The RFP requires allocation at gateway level, which
    #: means it has to be in scope wherever a call is made — not looked up
    #: afterwards from something that may since have changed.
    cost_center: str | None = None
    channel: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    #: The prompt in force, as ``PromptIdentity.as_metadata()``. Ambient
    #: rather than per-call so that code calling ``litellm`` directly — deep
    #: in a tool, in a library, anywhere the identity is not a local variable
    #: — still records which prompt version produced the answer. Without it
    #: prompt attribution only works on the call sites you remembered to
    #: annotate, which is precisely the gap governance cannot have.
    prompt: dict[str, Any] = field(default_factory=dict)

    #: Mutable holder for the trace's result. The context itself is frozen so
    #: nesting cannot corrupt a parent, but the *outcome* is only known at the
    #: end — so it lives in a one-slot dict the caller can fill:
    #:     with wt.trace("req") as ctx:
    #:         ctx.set_output({"answer": text})
    result: dict[str, Any] = field(default_factory=dict)

    def set_output(self, value: Any) -> None:
        """Record the trace's output. Without it Langfuse shows 'undefined'."""
        self.result["output"] = value

    def child(self, parent_id: str) -> TraceContext:
        """Context for work nested under ``parent_id``."""
        return replace(self, parent_id=parent_id)

    def merged_metadata(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        combined = dict(self.metadata)
        if extra:
            combined.update(extra)
        return combined

    def merged_tags(self, extra: list[str] | None = None) -> list[str]:
        if not extra:
            return list(self.tags)
        # dict.fromkeys preserves order while removing duplicates.
        return list(dict.fromkeys([*self.tags, *extra]))


# The default is None, not a TraceContext.
#
# A ContextVar default is one object shared by every context that has not set
# its own — and `result` is a mutable dict filled in by `set_output`. With a
# shared instance, output written while no trace was active stayed in that dict
# and every later unbound context read it back. Alice's answer, handed to Bob:
#
#     with bind(user_id="alice") as ctx: ctx.set_output("alice's answer")
#     with bind(user_id="bob") as ctx:   ctx.result  # {'output': "alice's answer"}
#
# In a library whose whole purpose is per-tenant attribution and masking, that
# is the worst possible place for a leak. `current_context()` now hands back a
# fresh context each time nothing is bound, so there is no shared dict to fill.
logger = logging.getLogger(__name__)

_context: ContextVar[TraceContext | None] = ContextVar(
    "shipit_watcher_context", default=None
)


def current_context() -> TraceContext:
    """The context in effect right now. Never ``None``.

    Unbound, this is a new empty context per call rather than a shared one, so
    writing to its ``result`` cannot be seen by the next caller.
    """
    return _context.get() or TraceContext()


def get_trace_id() -> str | None:
    return current_context().trace_id


def get_parent_id() -> str | None:
    return current_context().parent_id


@contextmanager
def bind(**fields: Any) -> Iterator[TraceContext]:
    """Bind values onto the ambient context for the duration of the block.

        with bind(company_id=str(company.id), cost_center="support-ops"):
            ...                       # every event here carries both

    ``tags`` and ``metadata`` merge with what is already bound; everything else
    replaces. Restoration uses the token from ``set()``, so it is correct even
    when blocks are nested or an exception unwinds several at once.
    """
    base = current_context()

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
        try:
            _context.reset(token)
        except ValueError:
            # The token was created in a DIFFERENT contextvars Context than the
            # one unwinding now, and `reset` refuses across Contexts.
            #
            # It happens whenever a generator that opened this block is closed
            # by the garbage collector instead of by its own frame — an SSE
            # response abandoned mid-stream is the usual way. The interpreter
            # throws GeneratorExit into the `yield` from whatever Context the
            # GC is running in, so `finally` executes somewhere `set()` never
            # ran. Surfaced as three "Exception ignored in: <generator object
            # Tracer.trace>" tracebacks per abandoned stream.
            #
            # Nothing needs undoing: the `set()` above only ever affected the
            # Context that is already gone, and this one never held `updated`.
            # Restoring `base` here would WRITE a value into a Context that
            # never had ours — worse than doing nothing. So: do nothing, and
            # say so at debug rather than tearing down a request over
            # bookkeeping.
            logger.debug(
                "shipit-watcher: context token belonged to another Context; "
                "nothing to restore", exc_info=True,
            )


@contextmanager
def use_prompt(prompt: Any) -> Iterator[None]:
    """Bind a prompt for the enclosing block.

    Every LLM call made inside — through :class:`~shipit_watcher.llm.LLMClient`
    *or* straight through ``litellm`` with ``instrument_litellm()`` active —
    records this prompt's name, version and fingerprint::

        prompt = wt.get_prompt("support-assistant")
        with wt.use_prompt(prompt):
            litellm.completion(model=..., messages=...)   # attributed

    Accepts a ``ManagedPrompt``, a ``PromptIdentity``, or the dict either
    produces, so callers do not have to know which layer handed it to them.
    """
    identity = getattr(prompt, "identity", prompt)
    payload = identity.as_metadata() if hasattr(identity, "as_metadata") else (identity or {})
    with bind(prompt=dict(payload)):
        yield
