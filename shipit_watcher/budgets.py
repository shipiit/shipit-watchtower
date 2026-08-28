"""Request-local cost budgets with warn, fallback and block policies.

Scope follows :mod:`contextvars`, so a budget applies to the logical flow of
execution — including across ``await`` and into ``asyncio`` tasks — without
being global state shared between concurrent requests.

A **plain thread does not inherit a context**, so a budget opened on the
request thread is invisible inside ``ThreadPoolExecutor`` work: ``check_budget``
finds nothing and a hard limit silently stops limiting. That is precisely where
a cost ceiling matters most — parallel tool fan-out and batch jobs. Two ways to
carry it across, both explicit:

    # 1. Let contextvars do it — the copied context sees the same budget.
    ctx = contextvars.copy_context()
    pool.submit(ctx.run, work)

    # 2. Or hand the state over and re-enter it on the worker.
    state = wt.current_budget()
    pool.submit(lambda: run_with(state))

    def run_with(state):
        with wt.use_budget(state):
            ...
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BudgetAction",
    "BudgetExceeded",
    "BudgetState",
    "budget",
    "charge_budget",
    "check_budget",
    "current_budget",
    "use_budget",
]


class BudgetAction(StrEnum):
    WARN = "warn"
    FALLBACK = "fallback"
    BLOCK = "block"


class BudgetExceeded(RuntimeError):
    """Raised before a model call when a hard cost budget is exhausted."""


@dataclass
class BudgetState:
    max_cost_usd: float
    action: BudgetAction = BudgetAction.BLOCK
    fallback_model: str = ""
    spent_usd: float = 0.0
    dimensions: dict[str, Any] = field(default_factory=dict)
    warned: bool = False
    #: One budget can be shared by concurrent work — an asyncio context copy
    #: hands over the same object, and :func:`use_budget` does so deliberately.
    #: ``+=`` is a read and a write, so without this two workers can post the
    #: same figure twice and a ceiling can be overrun by whatever was in
    #: flight.
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.max_cost_usd - self.spent_usd)

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.max_cost_usd

    def charge(self, cost_usd: float) -> float:
        """Add to the spend atomically and return the new total."""
        with self._lock:
            self.spent_usd += max(0.0, float(cost_usd or 0.0))
            return self.spent_usd


_budget: ContextVar[BudgetState | None] = ContextVar("watcher_budget", default=None)


def current_budget() -> BudgetState | None:
    return _budget.get()


@contextmanager
def budget(
    max_cost_usd: float,
    *,
    action: BudgetAction | str = BudgetAction.BLOCK,
    fallback_model: str = "",
    **dimensions: Any,
) -> Iterator[BudgetState]:
    """Apply a cost policy to model calls in this context."""
    state = BudgetState(
        max_cost_usd=max(0.0, float(max_cost_usd)),
        action=BudgetAction(action),
        fallback_model=fallback_model,
        dimensions=dimensions,
    )
    token = _budget.set(state)
    try:
        yield state
    finally:
        _budget.reset(token)


@contextmanager
def use_budget(state: BudgetState) -> Iterator[BudgetState]:
    """Re-enter an existing budget — the carrier for work on another thread.

    ``budget()`` opens a new policy; this one re-binds a policy that already
    exists, so a worker thread charges and checks against the *same* ceiling
    rather than starting a fresh, empty one.
    """
    token = _budget.set(state)
    try:
        yield state
    finally:
        _budget.reset(token)


def _policy(state: BudgetState, *, blocked: bool, reason: str) -> None:
    try:
        from .tracer import get_tracer

        get_tracer().policy(
            "cost_budget",
            blocked=blocked,
            reason=reason,
            max_cost_usd=state.max_cost_usd,
            spent_usd=state.spent_usd,
            **state.dimensions,
        )
    except Exception:
        logger.debug("watcher: could not record budget policy", exc_info=True)


def check_budget(estimated_cost_usd: float = 0.0) -> str | None:
    """Check before spending; return a configured fallback model if required."""
    state = current_budget()
    if state is None:
        return None
    projected = state.spent_usd + max(0.0, estimated_cost_usd)
    if projected < state.max_cost_usd:
        return None
    reason = (
        f"cost budget exhausted (${state.spent_usd:.6f} spent; "
        f"${state.max_cost_usd:.6f} limit)"
    )
    if state.action is BudgetAction.FALLBACK and state.fallback_model:
        _policy(state, blocked=False, reason=reason)
        return state.fallback_model
    if state.action is BudgetAction.WARN:
        if not state.warned:
            state.warned = True
            logger.warning("watcher: %s", reason)
            _policy(state, blocked=False, reason=reason)
        return None
    _policy(state, blocked=True, reason=reason)
    raise BudgetExceeded(reason)


def charge_budget(cost_usd: float) -> BudgetState | None:
    """Account for provider-reported cost after a successful operation."""
    state = current_budget()
    if state is not None:
        state.charge(cost_usd)
    return state
