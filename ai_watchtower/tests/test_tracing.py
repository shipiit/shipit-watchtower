"""
Tracer, context, events and decorators.

The invariants under test are the ones that make instrumentation trustworthy:
spans nest correctly, exceptions propagate unchanged, a broken sink cannot
break the caller, and a disabled tracer emits nothing at all.
"""

from __future__ import annotations

import asyncio

import pytest

import ai_watchtower as wt
from ai_watchtower.config import reset_config
from ai_watchtower.context import TraceContext, bind, current_context
from ai_watchtower.events import (
    DecisionEvent,
    Event,
    EventType,
    GenerationEvent,
    RetrievedChunk,
    Severity,
)
from ai_watchtower.tracer import Tracer


class RecordingSink:
    """Captures everything for assertions."""

    def __init__(self):
        self.traces, self.events, self.ended, self.flushed = [], [], [], 0

    def start_trace(self, trace_id, name, context, input_data=None):
        self.traces.append((trace_id, name, context))

    def end_trace(self, trace_id, output=None, metadata=None):
        self.ended.append((trace_id, output))

    def record(self, event, context):
        self.events.append((event, context))

    def flush(self):
        self.flushed += 1

    # helpers
    def names(self):
        return [e.name for e, _ in self.events]

    def by_name(self, name):
        return next(e for e, _ in self.events if e.name == name)


class ExplodingSink:
    """Fails on every call — the tracer must absorb all of it."""

    def start_trace(self, *a, **k): raise RuntimeError("sink down")
    def end_trace(self, *a, **k): raise RuntimeError("sink down")
    def record(self, *a, **k): raise RuntimeError("sink down")
    def flush(self): raise RuntimeError("sink down")


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    wt.configure(service_name="test", environment="test", mask_pii=True)
    yield
    reset_config()


@pytest.fixture
def sink():
    return RecordingSink()


@pytest.fixture
def tracer(sink):
    return Tracer(sinks=[sink])


@pytest.fixture
def installed_tracer(tracer):
    """Install `tracer` as the process-wide one, then restore.

    The decorators resolve the tracer through get_tracer() at call time, so
    exercising them means swapping the module global — and restoring it, or
    one test's tracer leaks into the next.
    """
    import ai_watchtower.tracer as tmod

    previous = tmod._tracer
    tmod._tracer = tracer
    try:
        yield tracer
    finally:
        tmod._tracer = previous


class TestContext:
    def test_default_is_empty(self):
        assert current_context().trace_id is None

    def test_bind_sets_and_restores(self):
        with bind(company_id="c1"):
            assert current_context().company_id == "c1"
        assert current_context().company_id is None

    def test_nesting_inherits(self):
        with bind(company_id="c1"):
            with bind(user_id="u1"):
                ctx = current_context()
                assert ctx.company_id == "c1" and ctx.user_id == "u1"

    def test_tags_merge_without_duplicates(self):
        with bind(tags=["a"]):
            with bind(tags=["a", "b"]):
                assert current_context().tags == ["a", "b"]

    def test_metadata_merges(self):
        with bind(metadata={"x": 1}):
            with bind(metadata={"y": 2}):
                assert current_context().metadata == {"x": 1, "y": 2}

    def test_unknown_fields_ignored(self):
        with bind(not_a_field="x"):
            assert current_context().trace_id is None

    def test_restored_after_exception(self):
        with pytest.raises(ValueError):
            with bind(company_id="c1"):
                raise ValueError
        assert current_context().company_id is None

    def test_child_returns_new_context(self):
        base = TraceContext(trace_id="t")
        assert base.child("p").parent_id == "p"
        assert base.parent_id is None  # original untouched


class TestTrace:
    def test_starts_and_ends(self, tracer, sink):
        with tracer.trace("req"):
            pass
        assert len(sink.traces) == 1 and len(sink.ended) == 1

    def test_binds_business_context(self, tracer, sink):
        with tracer.trace("req", company_id="acme", cost_center="ops"):
            ctx = current_context()
            assert ctx.company_id == "acme" and ctx.cost_center == "ops"

    def test_exception_propagates_unchanged(self, tracer):
        with pytest.raises(ValueError, match="boom"):
            with tracer.trace("req"):
                raise ValueError("boom")

    def test_exception_recorded_on_trace(self, tracer, sink):
        with pytest.raises(ValueError):
            with tracer.trace("req"):
                raise ValueError("boom")
        assert "ValueError" in str(sink.ended[0][1])

    def test_disabled_tracer_emits_nothing(self, sink):
        wt.configure(enabled=False)
        t = Tracer(sinks=[sink])
        with t.trace("req"):
            pass
        assert sink.traces == []

    def test_zero_sample_rate_skips(self, sink):
        wt.configure(sample_rate=0.0)
        t = Tracer(sinks=[sink])
        with t.trace("req"):
            pass
        assert sink.traces == []

    def test_broken_sink_does_not_break_caller(self):
        t = Tracer(sinks=[ExplodingSink()])
        with t.trace("req"):
            t.decision("d", chosen="a", options=["a", "b"])
        t.flush()  # must not raise


class TestSpans:
    def test_span_recorded(self, tracer, sink):
        with tracer.trace("req"):
            with tracer.span("work"):
                pass
        assert "work" in sink.names()

    def test_spans_nest(self, tracer, sink):
        with tracer.trace("req"):
            with tracer.span("outer") as outer:
                with tracer.span("inner") as inner:
                    pass
        assert inner.parent_id == outer.id

    def test_span_outside_trace_is_dropped(self, tracer, sink):
        with tracer.span("orphan"):
            pass
        assert sink.events == []

    def test_span_error_marked_and_reraised(self, tracer, sink):
        with pytest.raises(ValueError):
            with tracer.trace("req"):
                with tracer.span("work"):
                    raise ValueError("x")
        assert sink.by_name("work").severity == Severity.ERROR

    def test_duration_measured(self, tracer, sink):
        with tracer.trace("req"):
            with tracer.span("work") as span:
                span.output = "done"
        assert sink.by_name("work").duration_ms >= 0


class TestTypedEvents:
    def test_tool(self, tracer, sink):
        with tracer.trace("req"):
            with tracer.tool("search", arguments={"q": "x"}) as tool:
                tool.output = {"n": 1}
        event = sink.by_name("tool.search")
        assert event.type == EventType.TOOL_INVOCATION and event.tool_name == "search"

    def test_tool_failure_flagged(self, tracer, sink):
        with pytest.raises(RuntimeError):
            with tracer.trace("req"):
                with tracer.tool("bad"):
                    raise RuntimeError("nope")
        assert sink.by_name("tool.bad").succeeded is False

    def test_decision_records_alternatives(self, tracer, sink):
        with tracer.trace("req"):
            tracer.decision("route", chosen="a", options=["a", "b", "c"],
                            rationale="because", confidence=0.9)
        payload = sink.by_name("route").to_payload()
        assert payload["chosen"] == "a"
        assert payload["rejected"] == ["b", "c"]
        assert payload["confidence"] == 0.9

    def test_retrieval_carries_provenance(self, tracer, sink):
        with tracer.trace("req"):
            tracer.retrieval("kb", query="q", knowledge_base="kb1",
                             chunks=[RetrievedChunk(source="a.pdf", score=0.9,
                                                    content_hash="h1", version="2")])
        payload = sink.by_name("kb").to_payload()
        assert payload["chunk_count"] == 1
        assert payload["chunks"][0]["content_hash"] == "h1"

    def test_handoff(self, tracer, sink):
        with tracer.trace("req"):
            tracer.handoff(from_agent="a", to_agent="b", reason="expertise")
        assert sink.by_name("handoff.a->b").to_payload()["to_agent"] == "b"

    def test_policy_block_is_warning(self, tracer, sink):
        with tracer.trace("req"):
            tracer.policy("guard", blocked=True, reason="unregistered prompt")
        event = sink.by_name("policy.guard")
        assert event.to_payload()["blocked"] is True
        assert event.severity == Severity.WARNING

    def test_generation_usage_and_prompt(self, tracer, sink):
        prompt = wt.identify_prompt("hello", name="p", version="v1", registered=True)
        with tracer.trace("req"):
            with tracer.generation("llm", model="gpt-4o", prompt=prompt) as gen:
                gen.prompt_tokens, gen.completion_tokens, gen.total_cost = 10, 5, 0.01
        payload = sink.by_name("llm").to_payload()
        assert payload["total_tokens"] == 15
        assert payload["prompt_name"] == "p"
        assert payload["prompt_registered"] is True


class TestPrivacy:
    def test_input_masked(self, tracer, sink):
        with tracer.trace("req"):
            with tracer.span("s", input={"email": "a@b.pl"}):
                pass
        assert sink.by_name("s").input["email"] == "[EMAIL]"

    def test_capture_content_disabled(self, sink):
        wt.configure(capture_content=False)
        t = Tracer(sinks=[sink])
        with t.trace("req"):
            with t.span("s", input={"email": "a@b.pl"}):
                pass
        assert sink.by_name("s").input is None

    def test_long_content_truncated(self, sink):
        wt.configure(max_content_chars=50)
        t = Tracer(sinks=[sink])
        with t.trace("req"):
            with t.span("s", input="y" * 500):
                pass
        assert "[truncated]" in sink.by_name("s").input


class TestDecorators:
    def test_sync(self, installed_tracer, sink):
        @wt.observe("double")
        def double(x):
            return x * 2

        with installed_tracer.trace("req"):
            assert double(4) == 8
        assert "double" in sink.names()

    def test_async(self, installed_tracer, sink):
        @wt.observe("triple")
        async def triple(x):
            return x * 3

        async def run():
            with installed_tracer.trace("req"):
                return await triple(3)

        assert asyncio.run(run()) == 9
        assert "triple" in sink.names()

    def test_generator_counts_yields(self, installed_tracer, sink):
        @wt.observe("stream")
        def stream(n):
            yield from range(n)

        with installed_tracer.trace("req"):
            assert list(stream(3)) == [0, 1, 2]
        assert sink.by_name("stream").output == {"yielded": 3}

    def test_async_generator(self, installed_tracer, sink):
        @wt.observe("astream")
        async def astream(n):
            for i in range(n):
                yield i

        async def run():
            with installed_tracer.trace("req"):
                return [i async for i in astream(3)]

        assert asyncio.run(run()) == [0, 1, 2]
        assert sink.by_name("astream").output == {"yielded": 3}

    def test_tool_decorator(self, installed_tracer, sink):
        @wt.observe_tool("lookup")
        def lookup(q):
            return {"q": q}

        with installed_tracer.trace("req"):
            lookup("cars")
        assert sink.by_name("tool.lookup").tool_name == "lookup"

    def test_async_tool_decorator(self, installed_tracer, sink):
        @wt.observe_tool("alookup")
        async def alookup(q):
            return {"q": q}

        async def run():
            with installed_tracer.trace("req"):
                return await alookup("x")

        asyncio.run(run())
        assert sink.by_name("tool.alookup").succeeded is True

    def test_agent_decorator_opens_trace(self, installed_tracer, sink):
        @wt.observe_agent("planner")
        def run():
            return "ok"

        assert run() == "ok"
        assert sink.traces[0][1] == "agent.planner"

    def test_async_agent_decorator(self, installed_tracer, sink):
        @wt.observe_agent("aplanner")
        async def run():
            return "ok"

        assert asyncio.run(run()) == "ok"
        assert sink.traces[0][1] == "agent.aplanner"

    def test_decorator_reraises(self, installed_tracer, sink):
        @wt.observe("boom")
        def boom():
            raise KeyError("k")

        with pytest.raises(KeyError):
            with installed_tracer.trace("req"):
                boom()

    def test_name_defaults_to_qualname(self, installed_tracer, sink):
        @wt.observe()
        def helper():
            return 1

        with installed_tracer.trace("req"):
            helper()
        assert any("helper" in n for n in sink.names())


class TestIdentity:
    def test_fingerprint_stable(self):
        assert wt.fingerprint_text("hello") == wt.fingerprint_text("hello")

    def test_whitespace_insensitive(self):
        assert wt.fingerprint_text("a  b\n") == wt.fingerprint_text("a b")

    def test_content_change_detected(self):
        assert wt.fingerprint_text("a") != wt.fingerprint_text("b")

    def test_label_prefers_version(self):
        assert wt.identify_prompt("x", name="p", version="v2").label == "p@v2"

    def test_label_falls_back_to_fingerprint(self):
        assert wt.identify_prompt("x", name="p").label.startswith("p#")

    def test_unregistered_by_default(self):
        identity = wt.identify_prompt("x", name="p")
        assert identity.registered is False
        assert "prompt:unregistered" in identity.as_tags()


class TestConfig:
    def test_env_override(self, monkeypatch):
        reset_config()
        monkeypatch.setenv("WATCHTOWER_SERVICE", "svc-x")
        assert wt.get_config().service_name == "svc-x"

    def test_configure_wins(self):
        assert wt.configure(service_name="explicit").service_name == "explicit"

    def test_unknown_keys_ignored(self):
        wt.configure(not_real=123)  # must not raise

    def test_inactive_without_backends(self):
        reset_config()
        assert wt.configure(
            langfuse_public_key="", langfuse_secret_key="", persist_to_database=False
        ).is_active is False


class TestEvents:
    def test_duration_zero_before_finish(self):
        assert Event(name="x").duration_ms == 0

    def test_finish_sets_output(self):
        assert Event(name="x").finish(output="done").output == "done"

    def test_generation_total_tokens(self):
        gen = GenerationEvent(name="g", prompt_tokens=3, completion_tokens=4)
        assert gen.total_tokens == 7

    def test_decision_rejected_excludes_chosen(self):
        event = DecisionEvent(name="d", chosen="a", options_considered=["a", "b"])
        assert event.to_payload()["rejected"] == ["b"]

    def test_chunk_serialises(self):
        assert RetrievedChunk(source="s").to_dict()["source"] == "s"
