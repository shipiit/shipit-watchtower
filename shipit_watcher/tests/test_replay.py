from __future__ import annotations

from shipit_watcher.replay import (
    TraceBundle,
    TraceBundleSink,
    compare_bundles,
    load_bundle,
    replay_bundle,
)
from shipit_watcher.tracer import Tracer


def test_bundle_round_trip_is_privacy_sanitised(tmp_path):
    path = tmp_path / "trace.json"
    sink = TraceBundleSink(path)
    tracer = Tracer(sinks=[sink])
    with tracer.trace("request", input="ana@example.com") as context:
        with tracer.tool("lookup", arguments={"email": "ana@example.com"}):
            pass
        context.set_output("call ana@example.com")
    bundle = load_bundle(path)
    assert bundle.input == "[EMAIL]"
    assert bundle.output == "call [EMAIL]"
    assert bundle.events[0]["arguments"]["email"] == "[EMAIL]"


def test_replay_and_structural_diff():
    before = TraceBundle(input=2, output=4, events=[
        {"type": "generation", "total_cost": 0.01, "total_tokens": 10}
    ])
    after = TraceBundle(input=2, output=6, events=[
        {"type": "tool_invocation"},
        {"type": "generation", "total_cost": 0.02, "total_tokens": 14},
    ])
    assert replay_bundle(before, lambda value: value * 3) == 6
    diff = compare_bundles(before, after)
    assert diff["output_changed"] is True
    assert diff["event_types_changed"] is True
    assert diff["cost_delta"] == 0.01
    assert diff["token_delta"] == 4


def test_file_backed_sink_does_not_retain_every_trace(tmp_path):
    """A long-lived file-backed sink must not accumulate bundles in memory.

    Left in the dict, a debugging aid turns into a leak on any process that
    keeps the sink alive across requests.
    """
    sink = TraceBundleSink(tmp_path / "trace.json")
    tracer = Tracer(sinks=[sink])
    for index in range(3):
        with tracer.trace(f"turn-{index}"):
            pass
    assert sink.bundles == {}
    assert load_bundle(tmp_path / "trace.json").name == "turn-2"


def test_in_memory_sink_keeps_its_bundles():
    """Without a path, reading the bundles back is the whole API."""
    sink = TraceBundleSink()
    tracer = Tracer(sinks=[sink])
    with tracer.trace("turn"):
        pass
    assert len(sink.bundles) == 1
