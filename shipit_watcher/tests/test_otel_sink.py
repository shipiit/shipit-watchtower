"""
The OTLP sink — the one that makes Langfuse draw the agent graph.

Most of these assert on payload *shape*, which normally would not be worth
testing. Here it is the whole contract: the OTLP endpoint answers ``200`` to a
malformed span and the asynchronous ingestion job discards it seconds later.
There is no error to catch. Every bug fixed in this file presented identically
— a successful export followed by a 404 on lookup — so shape is checked here
rather than discovered in the UI.
"""

from __future__ import annotations

import json

import pytest

from shipit_watcher.config import reset_config
import shipit_watcher as wt
from shipit_watcher.context import TraceContext
from shipit_watcher.events import (
    Event,
    EventType,
    GenerationEvent,
    PolicyEvent,
    RetrievalEvent,
    Severity,
    ToolInvocationEvent,
)
from shipit_watcher.sinks.langfuse_otel_sink import (
    OBSERVATION_TYPES,
    LangfuseOTLPSink,
    _hex_id,
)
from shipit_watcher.tracer import Tracer


@pytest.fixture(autouse=True)
def _clean():
    reset_config()
    wt.configure(service_name="test", environment="test",
                 langfuse_public_key="pk", langfuse_secret_key="sk",
                 langfuse_host="https://langfuse.example")
    yield
    reset_config()


@pytest.fixture
def sink():
    """A sink that captures its export instead of sending it."""
    instance = LangfuseOTLPSink()
    instance.sent = []
    instance._send = lambda trace_id, spans: instance.sent.append((trace_id, spans))
    return instance


def attrs(span):
    """Attributes as a plain dict, unwrapping the OTLP value envelope."""
    out = {}
    for a in span["attributes"]:
        (kind, value), = a["value"].items()
        out[a["key"]] = value
    return out


def run(sink, name="turn", **context_fields):
    """Drive the sink through a tracer, as production does."""
    tracer = Tracer(sinks=[sink])
    return tracer


class TestIdentifiers:
    def test_watcher_ids_pass_through(self):
        assert _hex_id("a" * 32, 32) == "a" * 32

    def test_uuid_dashes_are_stripped(self):
        assert _hex_id("4492b210-a77f-4aad-82a9-a313a15ea46f", 32).isalnum()

    def test_non_hex_is_normalised_not_dropped(self):
        """An id from outside watcher — an email, a slug — must still
        produce a valid identifier: OTLP rejects malformed ones, and dropping
        the span would lose the observation entirely."""
        value = _hex_id("rahul@vivadrive.io", 16)
        assert len(value) == 16
        assert all(c in "0123456789abcdef" for c in value)

    def test_empty_gets_a_fresh_id(self):
        assert len(_hex_id("", 16)) == 16
        assert _hex_id("", 16) != _hex_id("", 16)

    def test_widths_are_exact(self):
        assert len(_hex_id("beef", 32)) == 32
        assert len(_hex_id("b" * 64, 16)) == 16


class TestObservationTypes:
    @pytest.mark.parametrize("event_type", list(EventType))
    def test_every_event_type_maps(self, event_type):
        """An unmapped type would silently become a graph-less span."""
        assert event_type in OBSERVATION_TYPES

    def test_tool_becomes_a_tool_node(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.tool("df_list_cars"):
                pass
        _, spans = sink.sent[0]
        tool = next(s for s in spans if "df_list_cars" in s["name"])
        assert attrs(tool)["langfuse.observation.type"] == "tool"

    def test_root_is_an_agent_node(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("fleetflow.agent.turn"):
            pass
        _, spans = sink.sent[0]
        assert attrs(spans[0])["langfuse.observation.type"] == "agent"

    def test_policy_becomes_a_guardrail(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            tracer.policy("prompt_registry", blocked=True, reason="unregistered")
        _, spans = sink.sent[0]
        types = {attrs(s)["langfuse.observation.type"] for s in spans}
        assert "guardrail" in types

    def test_retrieval_becomes_a_retriever(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            tracer.retrieval("kb.search", query="cars", chunks=[])
        _, spans = sink.sent[0]
        types = {attrs(s)["langfuse.observation.type"] for s in spans}
        assert "retriever" in types


class TestParenting:
    def test_top_level_events_attach_to_the_root(self, sink):
        """Left unparented they become a second root under one trace id, and
        the ingestion job discards the whole trace — 200 on export, 404 on
        lookup, nothing in between to explain it."""
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.tool("df_list_cars"):
                pass
        _, spans = sink.sent[0]
        root, child = spans[0], spans[1]
        assert "parentSpanId" not in root
        assert child["parentSpanId"] == root["spanId"]

    def test_exactly_one_root(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.tool("a"):
                pass
            with tracer.tool("b"):
                pass
        _, spans = sink.sent[0]
        assert sum(1 for s in spans if "parentSpanId" not in s) == 1

    def test_nesting_is_preserved(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.span("outer"):
                with tracer.span("inner"):
                    pass
        _, spans = sink.sent[0]
        by_name = {s["name"]: s for s in spans}
        assert by_name["inner"]["parentSpanId"] == by_name["outer"]["spanId"]

    def test_all_spans_share_the_trace_id(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.tool("a"):
                pass
        _, spans = sink.sent[0]
        assert len({s["traceId"] for s in spans}) == 1


class TestMetadataEncoding:
    """Metadata must be ONE JSON attribute, never dotted per-key.

    The dotted form is the bug that cost the most to find: the endpoint
    accepts it, and the trace then never appears.
    """

    def test_trace_metadata_is_a_single_attribute(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn", cost_center="fleet-ops", company_id="c1"):
            pass
        _, spans = sink.sent[0]
        keys = attrs(spans[0])
        assert "langfuse.trace.metadata" in keys
        assert not any(k.startswith("langfuse.trace.metadata.") for k in keys)

    def test_trace_metadata_is_valid_json(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn", cost_center="fleet-ops"):
            pass
        _, spans = sink.sent[0]
        parsed = json.loads(attrs(spans[0])["langfuse.trace.metadata"])
        assert parsed["cost_center"] == "fleet-ops"

    def test_observation_metadata_is_a_single_attribute(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.tool("df_list_cars"):
                pass
        _, spans = sink.sent[0]
        keys = attrs(spans[1])
        assert "langfuse.observation.metadata" in keys
        assert not any(k.startswith("langfuse.observation.metadata.") for k in keys)

    def test_prompt_link_is_a_single_attribute(self, sink):
        """Split into `…prompt.name` / `…prompt.version`, the ingestion job
        drops the generation and keeps the rest — a graph with the LLM call
        missing from it."""
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.generation("llm.x", model="m") as g:
                g.prompt = {"prompt_name": "fleet-assistant", "prompt_version": "2"}
        _, spans = sink.sent[0]
        keys = attrs(spans[1])
        assert not any(k.startswith("langfuse.observation.prompt.") for k in keys)
        link = json.loads(keys["langfuse.observation.prompt"])
        assert link == {"name": "fleet-assistant", "version": "2"}

    def test_prompt_link_omitted_when_unknown(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.generation("llm.x", model="m"):
                pass
        _, spans = sink.sent[0]
        assert "langfuse.observation.prompt" not in attrs(spans[1])


class TestTraceAttributes:
    def test_user_session_and_tags_are_carried(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn", user_id="rahul@vivadrive.io",
                          session_id="s1", tags=["fleetflow"]):
            pass
        _, spans = sink.sent[0]
        a = attrs(spans[0])
        assert a["langfuse.user.id"] == "rahul@vivadrive.io"
        assert a["langfuse.session.id"] == "s1"
        assert "fleetflow" in a["langfuse.trace.tags"]

    def test_user_id_may_be_any_string(self, sink):
        """Not every system keys users by UUID."""
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn", user_id="sso|abc-123"):
            pass
        _, spans = sink.sent[0]
        assert attrs(spans[0])["langfuse.user.id"] == "sso|abc-123"

    def test_output_reaches_the_root(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn") as ctx:
            ctx.set_output({"answer": "142 cars"})
        _, spans = sink.sent[0]
        assert "142 cars" in attrs(spans[0])["langfuse.trace.output"]

    def test_root_encloses_its_children_in_time(self, sink):
        """A root that ends before its children collapses the timeline."""
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.tool("slow"):
                pass
        _, spans = sink.sent[0]
        root = spans[0]
        for child in spans[1:]:
            assert int(root["startTimeUnixNano"]) <= int(child["startTimeUnixNano"])
            assert int(root["endTimeUnixNano"]) >= int(child["endTimeUnixNano"])


class TestGenerationAttributes:
    def test_usage_and_cost_are_recorded(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.generation("llm.x", model="gemini-2.5-flash") as g:
                g.prompt_tokens, g.completion_tokens = 467, 104
                g.total_cost = 0.0004
        _, spans = sink.sent[0]
        a = attrs(spans[1])
        assert a["gen_ai.request.model"] == "gemini-2.5-flash"
        assert a["gen_ai.usage.input_tokens"] == "467"
        assert json.loads(a["langfuse.observation.usage_details"])["total"] == 571
        assert json.loads(a["langfuse.observation.cost_details"])["total"] == 0.0004

    def test_zero_cost_is_omitted_not_asserted(self, sink):
        """Reporting $0 as a fact is worse than reporting nothing — it makes
        an unpriced model look free."""
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.generation("llm.x", model="m"):
                pass
        _, spans = sink.sent[0]
        assert "langfuse.observation.cost_details" not in attrs(spans[1])


class TestErrors:
    def test_error_severity_sets_the_otlp_status(self, sink):
        tracer = Tracer(sinks=[sink])
        with pytest.raises(ValueError):
            with tracer.trace("turn"):
                with tracer.span("boom"):
                    raise ValueError("no")
        _, spans = sink.sent[0]
        failed = next(s for s in spans if s["name"] == "boom")
        assert failed["status"]["code"] == 2

    def test_success_is_unset_not_ok(self, sink):
        """OK means "asserted successful", which a span that merely did not
        raise has not earned."""
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            with tracer.span("fine"):
                pass
        _, spans = sink.sent[0]
        assert next(s for s in spans if s["name"] == "fine")["status"]["code"] == 0


class TestResilience:
    def test_unavailable_without_credentials(self):
        reset_config()
        wt.configure(service_name="test", langfuse_public_key="",
                     langfuse_secret_key="", langfuse_host="")
        assert LangfuseOTLPSink().available is False

    def test_unavailable_sink_never_raises(self):
        reset_config()
        wt.configure(service_name="test", langfuse_public_key="",
                     langfuse_secret_key="", langfuse_host="")
        sink = LangfuseOTLPSink()
        context = TraceContext(trace_id="t1")
        sink.start_trace("t1", "turn", context)
        sink.record(Event(name="x"), context)
        sink.end_trace("t1")           # no exception is the assertion

    def test_export_failure_does_not_break_the_request(self, sink):
        def explode(*_args):
            raise RuntimeError("langfuse down")

        sink._send = explode
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):     # must complete regardless
            pass

    def test_empty_trace_sends_nothing_extra(self, sink):
        tracer = Tracer(sinks=[sink])
        with tracer.trace("turn"):
            pass
        assert len(sink.sent) == 1

    def test_flush_exports_an_unfinished_trace(self, sink):
        """A process exiting mid-turn should still yield a partial graph."""
        context = TraceContext(trace_id="a" * 32)
        sink.start_trace("a" * 32, "turn", context)
        sink.record(ToolInvocationEvent(name="tool.x", tool_name="x"), context)
        sink.flush()
        assert sink.sent and sink.sent[0][1]

    def test_events_without_a_trace_are_ignored(self, sink):
        sink.record(Event(name="orphan"), TraceContext())
        assert sink.sent == []
