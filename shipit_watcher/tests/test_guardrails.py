"""Content guardrails and the model price table.

The negative cases carry the weight here. A guardrail that fires on ordinary
business data gets switched off within a week, and a price table that invents
a rate for an unknown model produces a cost report nobody can trust.
"""

from __future__ import annotations

import pytest

import shipit_watcher as wt
from shipit_watcher.config import configure, reset_config
from shipit_watcher.guardrails import GuardrailViolation, check_content
from shipit_watcher.pricing import estimate_cost, get_model_price, set_model_price
from shipit_watcher.tracer import Tracer


class Collector:
    def __init__(self) -> None:
        self.events: list = []

    def start_trace(self, *args, **kwargs) -> None: ...
    def end_trace(self, *args, **kwargs) -> None: ...
    def record(self, event, context) -> None:
        self.events.append(event)
    def flush(self) -> None: ...


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


def test_off_by_default():
    """A guardrail that surprises people gets disabled globally the same day."""
    assert check_content("PESEL 44051401359") == {}


def test_audit_records_the_decision_without_blocking():
    configure(guardrail_mode="audit", sample_rate=1.0)
    sink = Collector()
    tracer = Tracer(sinks=[sink])
    import shipit_watcher.tracer as tracer_module

    previous, tracer_module._tracer = tracer_module._tracer, tracer
    try:
        with tracer.trace("request"):
            found = check_content("PESEL 44051401359")
    finally:
        tracer_module._tracer = previous

    assert found == {"PESEL": 1}
    event = sink.events[0]
    assert event.policy_name == "content_egress"
    assert event.blocked is False


def test_block_refuses_before_the_call_is_made():
    configure(guardrail_mode="block")
    with pytest.raises(GuardrailViolation) as raised:
        check_content({"messages": [{"content": "his PESEL is 44051401359"}]})
    assert raised.value.findings == {"PESEL": 1}
    assert "WATCHER_GUARDRAIL=audit" in str(raised.value)


def test_an_odometer_reading_is_not_a_tax_id():
    """The checksums are the whole reason this is usable.

    A guardrail built on bare patterns fires on every ten-digit number in a
    business database, and then it gets turned off.
    """
    configure(guardrail_mode="block")
    assert check_content("Odometer 1234567890 km") == {}
    assert check_content("Order 5551234 shipped, 40 units") == {}


def test_rules_can_be_narrowed_to_what_a_jurisdiction_requires():
    configure(guardrail_mode="block", guardrail_rules="IBAN")
    # An email is detected by default, but not when the rule set is narrowed.
    assert check_content("write to jan@example.pl") == {}
    with pytest.raises(GuardrailViolation):
        check_content("PL61109010140000071219812874")


def test_guard_blocks_regardless_of_configuration():
    """Some call sites are never "record it and carry on"."""
    configure(guardrail_mode="off")
    assert wt.guard("nothing sensitive here") == "nothing sensitive here"
    with pytest.raises(GuardrailViolation):
        wt.guard("jan@example.pl")


# ── pricing ──────────────────────────────────────────────────────────────────

def test_an_unknown_model_reports_no_cost_rather_than_a_guess():
    """An invented number looks exactly as confident as a real one."""
    assert estimate_cost("acme-internal-7b", 1000, 500) == 0.0
    assert get_model_price("acme-internal-7b") is None


def test_a_routing_prefix_is_not_part_of_the_model_name():
    assert estimate_cost("openai/gpt-4o", 1000, 500) == estimate_cost("gpt-4o", 1000, 500)


def test_a_dated_release_prices_as_its_family():
    assert estimate_cost("claude-sonnet-4-5-20260101", 1000, 500) == estimate_cost(
        "claude-sonnet-4-5", 1000, 500
    )


def test_the_longest_matching_prefix_wins():
    """`gpt-4.1-mini` must not price as `gpt-4.1`."""
    assert estimate_cost("gpt-4.1-mini", 1_000_000, 0) == pytest.approx(0.4)
    assert estimate_cost("gpt-4.1", 1_000_000, 0) == pytest.approx(2.0)


def test_a_local_model_can_assert_that_it_is_free():
    """An asserted zero and an unknown are different facts."""
    set_model_price("acme-internal-7b", input=0.0, output=0.0)
    assert get_model_price("acme-internal-7b") is not None
    assert estimate_cost("acme-internal-7b", 1000, 500) == 0.0


def test_cached_input_is_billed_at_its_own_rate():
    price = get_model_price("gpt-4o")
    assert price is not None
    # 200 fresh + 800 cached, not 1000 fresh.
    assert price.cost(1000, 0, cached_tokens=800) == pytest.approx(
        (200 * 2.5 + 800 * 1.25) / 1e6
    )
