from __future__ import annotations

from typing import Any

from shipit_watcher.scoring import Score
from shipit_watcher.sinks.dashboard_sink import DashboardSink
from shipit_watcher.tracer import Tracer


def test_dashboard_exports_real_bundle_before_its_scores(monkeypatch):
    sent: list[tuple[str, dict[str, Any]]] = []
    sink = DashboardSink(url="https://watcher.example")
    monkeypatch.setattr(sink, "_send", lambda path, payload: sent.append((path, payload)))
    tracer = Tracer(sinks=[sink])

    with tracer.trace("request", input={"email": "ana@example.com"}) as context:
        with tracer.generation("answer", model="gpt-5.4") as generation:
            generation.prompt_tokens = 10
            generation.completion_tokens = 4
            generation.total_cost = 0.002
            generation.output = "done"
        sink.record_score(Score("quality", 0.9))
        context.set_output("done")
    sink.flush()

    assert [path for path, _ in sent] == ["/api/ingest", "/api/scores"]
    bundle = sent[0][1]
    assert bundle["schema_version"] == "watcher.trace.v1"
    assert bundle["input"]["email"] == "[EMAIL]"
    assert bundle["events"][0]["model"] == "gpt-5.4"
    assert sent[1][1]["trace_id"] == bundle["trace_id"]


def test_dashboard_bundle_preserves_error_status_message(monkeypatch):
    sent: list[tuple[str, dict[str, Any]]] = []
    sink = DashboardSink(url="https://watcher.example")
    monkeypatch.setattr(sink, "_send", lambda path, payload: sent.append((path, payload)))
    tracer = Tracer(sinks=[sink])

    try:
        with tracer.trace("request"), tracer.tool("explode"):
            raise ValueError("failed")
    except ValueError:
        pass
    sink.flush()

    event = sent[0][1]["events"][0]
    assert event["severity"] == "ERROR"
    assert event["status_message"] == "ValueError: failed"
