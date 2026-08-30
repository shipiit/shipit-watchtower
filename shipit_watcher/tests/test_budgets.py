from __future__ import annotations

import pytest

from shipit_watcher.budgets import (
    BudgetExceeded,
    budget,
    charge_budget,
    check_budget,
    current_budget,
)


def test_hard_budget_blocks_the_next_operation():
    with budget(0.01) as state:
        charge_budget(0.01)
        with pytest.raises(BudgetExceeded):
            check_budget()
        assert state.remaining_usd == 0


def test_budget_can_route_to_a_cheaper_fallback():
    with budget(0.01, action="fallback", fallback_model="openai/gpt-4.1-mini"):
        charge_budget(0.02)
        assert check_budget() == "openai/gpt-4.1-mini"


def test_budget_is_context_local_and_restored():
    assert current_budget() is None
    with budget(1.0, tenant="acme") as state:
        assert current_budget() is state
        assert state.dimensions == {"tenant": "acme"}
    assert current_budget() is None
