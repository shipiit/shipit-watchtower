"""
Gateway propagation.

A LiteLLM proxy sees an HTTP request, not the caller's contextvars. Everything
the gateway needs to allocate cost or enforce prompt governance therefore has
to travel in the request body — and ``metadata`` does not survive the trip
(litellm treats it as a client-side field and drops it), so ``extra_body`` is
the only route. These tests pin that contract: what leaves the process, under
which names, and who is allowed to write the generation record.
"""

from __future__ import annotations

import pytest

from shipit_watcher.config import configure, reset_config
from shipit_watcher.context import TraceContext, bind
from shipit_watcher.instrumentation.litellm import (
    _generation_owner,
    _governance_payload,
    _stamp,
)


@pytest.fixture(autouse=True)
def _clean():
    """Restore config *and* the ambient context.

    These tests set the contextvar directly, which is the only way to exercise
    ``_stamp`` in isolation — but a contextvar set without its token stays set
    for everything that runs afterwards in the same process. Resetting it here
    keeps the leak inside this module.
    """
    from shipit_watcher.context import _context

    reset_config()
    token = _context.set(TraceContext())
    yield
    _context.reset(token)
    reset_config()


def _ctx(**over):
    base = dict(trace_id="trace-1", cost_center="DF-PROD-100",
                company_id="fleet-7", session_id="sess-9", user_id="u-1")
    base.update(over)
    return TraceContext(**base)


# ── what reaches the gateway ────────────────────────────────────────────

class TestGovernancePayload:

    def test_maps_sdk_vocabulary_onto_gateway_wire_names(self):
        configure(service_name="fleetflow", environment="production")
        payload = _governance_payload(_ctx())
        assert payload["system_id"] == "fleetflow"
        assert payload["environment"] == "production"
        assert payload["mpk"] == "DF-PROD-100"
        assert payload["client_id"] == "fleet-7"

    def test_prompt_identity_keeps_its_registry_names(self):
        """Renaming these would break a gateway enforcing on prompt_name."""
        ctx = _ctx(prompt={"prompt_name": "df/agent", "prompt_version": 3,
                           "prompt_fingerprint": "abc123", "prompt_registered": True})
        payload = _governance_payload(ctx)
        assert payload["prompt_name"] == "df/agent"
        assert payload["prompt_version"] == "3"        # stringified for the wire
        assert payload["prompt_fingerprint"] == "abc123"
        assert payload["prompt_registered"] is True    # stays a bool

    def test_absent_dimensions_are_omitted_not_sent_empty(self):
        payload = _governance_payload(TraceContext(trace_id="t"))
        assert "mpk" not in payload and "client_id" not in payload

    def test_key_map_is_overridable_for_a_foreign_gateway(self):
        configure(service_name="fleetflow",
                  gateway_key_map={"tenant": "company_id", "cc": "cost_center"})
        payload = _governance_payload(_ctx())
        assert payload == {"tenant": "fleet-7", "cc": "DF-PROD-100"}
        assert "system_id" not in payload

    def test_disabled_sends_nothing(self):
        configure(gateway_attribution=False)
        assert _governance_payload(_ctx()) == {}


# ── how it is stamped onto the outgoing call ────────────────────────────

class TestStamp:

    def test_attribution_travels_in_extra_body(self):
        configure(service_name="fleetflow", environment="production")
        with bind(cost_center="DF-PROD-100", company_id="fleet-7"):
            from shipit_watcher.context import _context
            _context.set(_ctx())
            out = _stamp({"model": "gpt-4o", "messages": []})

        forwarded = out["extra_body"]["metadata"]
        assert forwarded["existing_trace_id"] == "trace-1"     # unchanged behaviour
        assert forwarded["mpk"] == "DF-PROD-100"               # new
        assert forwarded["client_id"] == "fleet-7"
        assert forwarded["system_id"] == "fleetflow"

    def test_caller_supplied_values_are_not_overridden(self):
        configure(service_name="fleetflow")
        from shipit_watcher.context import _context
        _context.set(_ctx())
        out = _stamp({"model": "m", "messages": [],
                      "extra_body": {"metadata": {"mpk": "RECZNIE-USTAWIONE"}}})
        assert out["extra_body"]["metadata"]["mpk"] == "RECZNIE-USTAWIONE"

    def test_untraced_call_is_left_alone(self):
        from shipit_watcher.context import _context
        _context.set(TraceContext())
        out = _stamp({"model": "m", "messages": []})
        assert "extra_body" not in out

    def test_disabled_still_forwards_trace_joining_keys(self):
        """Turning attribution off must not detach the trace."""
        configure(gateway_attribution=False)
        from shipit_watcher.context import _context
        _context.set(_ctx())
        forwarded = _stamp({"model": "m", "messages": []})["extra_body"]["metadata"]
        assert forwarded["existing_trace_id"] == "trace-1"
        assert "mpk" not in forwarded


# ── who writes the generation ───────────────────────────────────────────

class TestGenerationOwner:

    def test_defaults_to_the_application(self):
        assert _generation_owner() == "app"

    def test_gateway_mode_recognised(self):
        configure(generation_owner="gateway")
        assert _generation_owner() == "gateway"

    def test_unknown_value_falls_back_to_app(self):
        """A typo must not silently stop generations being recorded at all."""
        configure(generation_owner="proxy-ish")
        assert _generation_owner() == "app"

    def test_gateway_mode_suppresses_the_duplicate_generation(self, monkeypatch):
        from shipit_watcher.instrumentation.litellm import WatcherLiteLLMHandler

        emitted = []
        import shipit_watcher.tracer as tracer_mod

        class _Tracer:
            def _emit(self, event, context):
                emitted.append(event)

        monkeypatch.setattr(tracer_mod, "get_tracer", lambda: _Tracer())
        monkeypatch.setattr(
            "shipit_watcher.instrumentation.litellm.get_tracer", lambda: _Tracer())

        from shipit_watcher.context import _context
        _context.set(_ctx())
        handler = WatcherLiteLLMHandler()
        kwargs = {"model": "gpt-4o", "call_type": "completion"}

        configure(generation_owner="app")
        handler.log_success_event(kwargs, None, 0, 1)
        assert len(emitted) == 1, "app mode must record the generation"

        emitted.clear()
        configure(generation_owner="gateway")
        handler.log_success_event(kwargs, None, 0, 1)
        assert emitted == [], "gateway mode must not duplicate the gateway's record"

    def test_failures_are_recorded_even_in_gateway_mode(self, monkeypatch):
        """A rejected call never reached the gateway's logger — keep ours."""
        from shipit_watcher.instrumentation.litellm import WatcherLiteLLMHandler

        emitted = []

        class _Tracer:
            def _emit(self, event, context):
                emitted.append(event)

        monkeypatch.setattr(
            "shipit_watcher.instrumentation.litellm.get_tracer", lambda: _Tracer())

        from shipit_watcher.context import _context
        _context.set(_ctx())
        configure(generation_owner="gateway")
        # litellm reports the failure through kwargs["exception"], not the
        # response object — that is where the handler reads it from.
        WatcherLiteLLMHandler().log_failure_event(
            {"model": "gpt-4o", "call_type": "completion",
             "exception": RuntimeError("PROMPT_NOT_IN_REGISTRY")},
            None, 0, 1)
        assert len(emitted) == 1
