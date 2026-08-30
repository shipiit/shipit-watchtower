from __future__ import annotations

import pytest

from shipit_watcher.backends import Backend, LangSmithBackend, PhoenixBackend
from shipit_watcher.config import WatcherConfig, reset_config
from shipit_watcher.context import bind, extract_trace_context, inject_trace_context
from shipit_watcher.setup import doctor
from shipit_watcher.sinks.langsmith_sink import LangSmithOTLPSink
from shipit_watcher.sinks.openinference_otel_sink import OpenInferenceOTLPSink
from shipit_watcher.sinks.phoenix_sink import PhoenixOTLPSink
from shipit_watcher.tracer import Tracer


class Collector:
    def __init__(self):
        self.started = []
        self.ended = []
        self.payloads = []

    def start_trace(self, *args):
        self.started.append(args)

    def end_trace(self, *args):
        self.ended.append(args)

    def record(self, event, context):
        self.payloads.append(event.to_payload())

    def flush(self):
        return None


@pytest.fixture(autouse=True)
def clean_config():
    reset_config()
    yield
    reset_config()


def test_w3c_context_round_trip():
    with bind(trace_id="a" * 32, root_span_id="b" * 16, sampled=True):
        headers = inject_trace_context({"x-request-id": "r1"})
    assert headers["x-request-id"] == "r1"
    assert headers["traceparent"] == f"00-{'a' * 32}-{'b' * 16}-01"
    assert extract_trace_context(headers) == {
        "trace_id": "a" * 32,
        "remote_parent_id": "b" * 16,
        "sampled": True,
    }


def test_invalid_w3c_context_is_ignored():
    assert extract_trace_context({"TraceParent": "not-valid"}) == {}


def test_sampled_out_error_is_promoted():
    sink = Collector()
    tracer = Tracer(sinks=[sink], config=WatcherConfig(sample_rate=0.0))
    with pytest.raises(RuntimeError, match="boom"), tracer.trace("request"):
        raise RuntimeError("boom")
    assert len(sink.started) == len(sink.ended) == 1
    assert sink.payloads == []
    assert sink.ended[0][1] == {"error": "RuntimeError: boom"}


def test_sampled_out_success_stays_silent():
    sink = Collector()
    tracer = Tracer(sinks=[sink], config=WatcherConfig(sample_rate=0.0))
    with tracer.trace("request"):
        pass
    assert sink.started == sink.ended == sink.payloads == []


def test_sampled_out_policy_block_is_tail_promoted():
    sink = Collector()
    tracer = Tracer(sinks=[sink], config=WatcherConfig(sample_rate=0.0))
    with tracer.trace("request"):
        tracer.policy("model_allowlist", blocked=True, reason="model denied")
    assert len(sink.started) == len(sink.ended) == 1
    assert sink.payloads[0]["event_type"] == "policy"
    assert sink.payloads[0]["blocked"] is True


def test_sampled_out_expensive_trace_is_tail_promoted():
    sink = Collector()
    tracer = Tracer(
        sinks=[sink],
        config=WatcherConfig(sample_rate=0.0, expensive_trace_usd=0.05),
    )
    with tracer.trace("request"), tracer.generation("llm", model="large") as event:
        event.total_cost = 0.08
    assert len(sink.started) == 1
    assert sink.payloads[0]["total_cost"] == 0.08


def test_tool_failure_fields_exist_before_immediate_serialisation():
    sink = Collector()
    tracer = Tracer(sinks=[sink])
    with pytest.raises(ValueError), tracer.trace("request"), tracer.tool("explode"):
        raise ValueError("bad@example.com")
    assert sink.payloads[0]["succeeded"] is False
    assert sink.payloads[0]["error"] == "[EMAIL]"


def test_typed_event_content_is_redacted_centrally():
    sink = Collector()
    tracer = Tracer(sinks=[sink])
    with tracer.trace("request"):
        tracer.decision(
            "route", chosen="jan@example.com", options=["jan@example.com", "safe"],
            rationale="contact jan@example.com",
        )
    assert sink.payloads[0]["chosen"] == "[EMAIL]"
    assert sink.payloads[0]["rationale"] == "contact [EMAIL]"


def test_openinference_sink_builds_one_tree():
    sink = OpenInferenceOTLPSink("http://collector.test")
    sent = []
    sink._send = sent.append
    tracer = Tracer(sinks=[sink])
    with tracer.trace("agent.turn"), tracer.tool("search"):
        pass
    spans = sent[0]
    assert len(spans) == 2
    assert spans[1]["parentSpanId"] == spans[0]["spanId"]
    kind = next(
        a["value"]["stringValue"] for a in spans[1]["attributes"]
        if a["key"] == "openinference.span.kind"
    )
    assert kind == "TOOL"


def test_openinference_root_continues_remote_parent():
    sink = OpenInferenceOTLPSink("http://collector.test")
    sent = []
    sink._send = sent.append
    tracer = Tracer(sinks=[sink])
    remote = {"trace_id": "a" * 32, "remote_parent_id": "b" * 16}
    with tracer.trace("continued", **remote):
        pass
    assert sent[0][0]["traceId"] == "a" * 32
    assert sent[0][0]["parentSpanId"] == "b" * 16


def test_backend_capabilities_are_structural():
    backend = Backend(name="custom", trace=OpenInferenceOTLPSink("http://otel"))
    assert backend.capabilities.traces is True
    assert backend.capabilities.prompts is False


def test_langfuse_otlp_is_the_safe_default():
    assert WatcherConfig().langfuse_transport == "otlp"


def test_langsmith_emits_native_kind_and_experiment_attributes(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "secret")
    reset_config()
    sink = LangSmithOTLPSink()
    sent = []
    sink._send = sent.append
    tracer = Tracer(sinks=[sink])
    with tracer.trace(
        "experiment.prompt-v2",
        metadata={"run_name": "prompt-v2", "item_id": "example-7"},
    ), tracer.tool("search"):
        pass
    root_attrs = {a["key"]: a["value"] for a in sent[0][0]["attributes"]}
    child_attrs = {a["key"]: a["value"] for a in sent[0][1]["attributes"]}
    assert root_attrs["langsmith.trace.session_id"]["stringValue"] == "prompt-v2"
    assert root_attrs["langsmith.reference_example_id"]["stringValue"] == "example-7"
    assert child_attrs["langsmith.span.kind"]["stringValue"] == "tool"


def test_langsmith_prefers_conversation_session_and_exports_tags(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "secret")
    reset_config()
    sink = LangSmithOTLPSink()
    sent = []
    sink._send = sent.append
    tracer = Tracer(sinks=[sink])
    with tracer.trace(
        "agent.turn",
        session_id="thread-42",
        tags=["production", "support"],
        metadata={"run_name": "experiment-that-must-not-win"},
    ):
        pass
    root_attrs = {a["key"]: a["value"] for a in sent[0][0]["attributes"]}
    assert root_attrs["langsmith.trace.session_id"]["stringValue"] == "thread-42"
    assert root_attrs["langsmith.span.tags"]["stringValue"] == "production,support"


def test_explicit_backend_factories(monkeypatch):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006")
    monkeypatch.setenv("LANGSMITH_API_KEY", "secret")
    reset_config()
    phoenix = PhoenixBackend.from_env()
    langsmith = LangSmithBackend.from_env()
    assert isinstance(phoenix.trace, PhoenixOTLPSink)
    assert isinstance(langsmith.trace, LangSmithOTLPSink)
    if phoenix.prompts is not None:
        assert phoenix.capabilities.experiments
        assert phoenix.capabilities.annotations
    if langsmith.prompts is not None:
        assert langsmith.capabilities.experiments
        assert langsmith.capabilities.annotations


def test_phoenix_auto_configuration(monkeypatch):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006")
    monkeypatch.setenv("PHOENIX_PROJECT_NAME", "support")
    reset_config()
    sink = PhoenixOTLPSink()
    assert sink.available
    assert sink._endpoint == "http://phoenix:6006/v1/traces"
    assert sink._headers["x-project-name"] == "support"


def test_langsmith_requires_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    reset_config()
    assert LangSmithOTLPSink().available is False


def test_doctor_reports_all_three_backends(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls")
    reset_config()
    report = doctor()
    configured = {status.name for status in report.backends if status.configured}
    assert {"langfuse", "phoenix", "langsmith"} <= configured


def test_content_policy_metadata_drops_event_content():
    sink = Collector()
    tracer = Tracer(
        sinks=[sink], config=WatcherConfig(content_policy="metadata")
    )
    with (
        tracer.trace("request", input="secret@example.com"),
        tracer.span("work", input="secret@example.com", owner="a@example.com"),
    ):
        pass
    assert sink.started[0][3] is None
    assert sink.payloads[0]["owner"] == "[EMAIL]"


def test_unmasked_capture_is_a_note_not_a_warning(monkeypatch):
    """`assert report.ok` must survive a deliberate trusted-environment opt-in.

    The README tells applications to assert on `ok` at startup, so a documented
    choice cannot be the thing that fails the boot.
    """
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("WATCHER_CONTENT_POLICY", "full")
    reset_config()
    report = doctor()
    assert report.ok is True
    assert report.warnings == []
    assert any("unmasked" in note for note in report.notes)


def test_full_policy_via_legacy_switch_is_also_reported(monkeypatch):
    """`mask_pii=False` and `content_policy="full"` reach the same state.

    Flagging only the legacy spelling let the documented one pass silently.
    """
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("WATCHER_MASK_PII", "false")
    reset_config()
    assert any("unmasked" in note for note in doctor().notes)


def test_explicit_policy_overrides_the_legacy_switch(monkeypatch):
    """`content_policy=redacted` masks, whatever `mask_pii` says — so no note."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("WATCHER_MASK_PII", "false")
    monkeypatch.setenv("WATCHER_CONTENT_POLICY", "redacted")
    reset_config()
    assert doctor().notes == []
