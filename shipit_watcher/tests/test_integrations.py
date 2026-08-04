"""
Sinks and third-party instrumentation.

Exercised against fakes rather than live services. That is not a compromise:
the behaviour worth pinning is how the SDK reacts to a backend — including a
backend that misbehaves — and a real Langfuse would make those cases hard to
provoke and slow to run.

The LiteLLM tests matter most. They pin the fix for the duplicate-trace problem:
``instrument()`` must remove LiteLLM's own ``"langfuse"`` callback, or every
call is recorded twice.
"""

from __future__ import annotations

import sys
import types

import pytest

import shipit_watcher as wt
from shipit_watcher.config import reset_config
from shipit_watcher.context import TraceContext
from shipit_watcher.events import Event, EventType, GenerationEvent, Severity
from shipit_watcher.sinks.base import ConsoleSink, FanOutSink


@pytest.fixture(autouse=True)
def _clean():
    reset_config()
    yield
    reset_config()


# ── FanOutSink ───────────────────────────────────────────────────────────

class _Good:
    def __init__(self):
        self.calls = []

    def start_trace(self, *a, **k): self.calls.append("start")
    def end_trace(self, *a, **k): self.calls.append("end")
    def record(self, *a, **k): self.calls.append("record")
    def flush(self): self.calls.append("flush")


class _Bad:
    def start_trace(self, *a, **k): raise RuntimeError
    def end_trace(self, *a, **k): raise RuntimeError
    def record(self, *a, **k): raise RuntimeError
    def flush(self): raise RuntimeError


class TestFanOut:
    def test_broadcasts_to_all(self):
        a, b = _Good(), _Good()
        fan = FanOutSink([a, b])
        fan.start_trace("t", "n", TraceContext())
        fan.record(Event(name="e"), TraceContext())
        fan.end_trace("t")
        fan.flush()
        assert a.calls == b.calls == ["start", "record", "end", "flush"]

    def test_one_failure_does_not_stop_the_others(self):
        """The reason Langfuse being down must not cost you the DB row."""
        good = _Good()
        fan = FanOutSink([_Bad(), good])
        fan.start_trace("t", "n", TraceContext())
        fan.record(Event(name="e"), TraceContext())
        fan.end_trace("t")
        fan.flush()
        assert good.calls == ["start", "record", "end", "flush"]

    def test_none_entries_dropped(self):
        assert bool(FanOutSink([None])) is False

    def test_empty_is_falsey(self):
        assert bool(FanOutSink([])) is False


class TestConsoleSink:
    def test_full_lifecycle_is_quiet(self, caplog):
        sink = ConsoleSink()
        ctx = TraceContext(trace_id="t", company_id="acme")
        sink.start_trace("t", "req", ctx)
        sink.record(Event(name="e").finish(), ctx)
        sink.record(Event(name="err", severity=Severity.ERROR).finish(), ctx)
        sink.end_trace("t")
        sink.flush()  # no exception is the assertion


# ── Langfuse sink ────────────────────────────────────────────────────────

class _FakeLangfuseTrace:
    def __init__(self):
        self.generations, self.spans, self.updates = [], [], []

    def generation(self, **kw): self.generations.append(kw)
    def span(self, **kw):
        self.spans.append(kw)
        return types.SimpleNamespace(end=lambda: None)
    def update(self, **kw): self.updates.append(kw)


class _FakeLangfuseClient:
    def __init__(self):
        self.traces, self.flushed = [], 0

    def trace(self, **kw):
        node = _FakeLangfuseTrace()
        self.traces.append((kw, node))
        return node

    def flush(self): self.flushed += 1


class TestLangfuseSink:
    def _sink(self):
        from shipit_watcher.sinks.langfuse_sink import LangfuseSink

        client = _FakeLangfuseClient()
        return LangfuseSink(client=client), client

    def test_unavailable_without_client(self):
        from shipit_watcher.sinks.langfuse_sink import LangfuseSink

        sink = LangfuseSink(client=None)
        # Nothing should raise even though there is no backend.
        sink.start_trace("t", "n", TraceContext(trace_id="t"))
        sink.record(Event(name="e"), TraceContext(trace_id="t"))
        sink.end_trace("t")
        sink.flush()
        assert sink.available is False

    def test_trace_carries_tenant_and_mpk(self):
        sink, client = self._sink()
        ctx = TraceContext(trace_id="t", company_id="acme", cost_center="ops",
                           user_id="u1", session_id="s1")
        sink.start_trace("t", "chat", ctx)
        kwargs, _ = client.traces[0]
        assert kwargs["user_id"] == "u1"
        assert kwargs["metadata"]["cost_center"] == "ops"
        assert "company:acme" in kwargs["tags"]
        assert "mpk:ops" in kwargs["tags"]

    def test_generation_uses_generation_api(self):
        sink, client = self._sink()
        ctx = TraceContext(trace_id="t")
        sink.start_trace("t", "chat", ctx)
        event = GenerationEvent(name="llm", model="gpt-4o",
                                prompt_tokens=3, completion_tokens=4).finish()
        sink.record(event, ctx)
        sink.end_trace("t")          # events are buffered until the trace closes
        _, node = client.traces[0]
        assert node.generations[0]["model"] == "gpt-4o"
        assert node.generations[0]["usage"]["total"] == 7

    def test_plain_event_uses_span_api(self):
        sink, client = self._sink()
        ctx = TraceContext(trace_id="t")
        sink.start_trace("t", "chat", ctx)
        sink.record(Event(name="work", type=EventType.SPAN).finish(), ctx)
        sink.end_trace("t")          # events are buffered until the trace closes
        _, node = client.traces[0]
        assert node.spans[0]["name"] == "work"

    def test_children_nest_under_their_parent(self):
        """The graph fix: a child must be created on its parent's handle, not
        on the trace. Events arrive child-first, so this only works because
        the sink buffers and flushes parents first."""
        sink, client = self._sink()
        ctx = TraceContext(trace_id="t")
        sink.start_trace("t", "chat", ctx)

        parent = Event(name="outer").finish()
        child = Event(name="inner")
        child.parent_id = parent.id
        child.finish()

        sink.record(child, ctx)      # child arrives first, as in real use
        sink.record(parent, ctx)
        sink.end_trace("t")

        _, node = client.traces[0]
        # Only the root was created on the trace; the child went on the parent.
        assert [s["name"] for s in node.spans] == ["outer"]

    def test_end_trace_updates(self):
        sink, client = self._sink()
        ctx = TraceContext(trace_id="t")
        sink.start_trace("t", "chat", ctx)
        sink.end_trace("t", output={"ok": True})
        _, node = client.traces[0]
        assert node.updates[0]["output"] == {"ok": True}

    def test_end_unknown_trace_is_noop(self):
        sink, _ = self._sink()
        sink.end_trace("missing")  # must not raise

    def test_flush_delegates(self):
        sink, client = self._sink()
        sink.flush()
        assert client.flushed == 1

    def test_backend_exception_absorbed(self):
        from shipit_watcher.sinks.langfuse_sink import LangfuseSink

        class Boom:
            def trace(self, **kw): raise RuntimeError("down")
            def flush(self): raise RuntimeError("down")

        sink = LangfuseSink(client=Boom())
        sink.start_trace("t", "n", TraceContext(trace_id="t"))
        sink.flush()  # absorbed


# ── LiteLLM instrumentation ──────────────────────────────────────────────

@pytest.fixture
def fake_litellm(monkeypatch):
    """A stand-in litellm module, installed for the duration of a test."""
    module = types.ModuleType("litellm")
    module.success_callback = ["langfuse"]
    module.failure_callback = ["langfuse"]
    module.callbacks = []
    module.completion_cost = lambda **kw: 0.005
    monkeypatch.setitem(sys.modules, "litellm", module)

    from shipit_watcher.instrumentation import litellm as inst

    inst._instrumented = False
    inst._saved_callbacks.clear()
    yield module
    inst._instrumented = False


class TestLiteLLMInstrumentation:
    def test_removes_langfuse_callback(self, fake_litellm):
        """The duplicate-trace fix, pinned."""
        from shipit_watcher.instrumentation import litellm as inst

        assert inst.instrument() is True
        assert "langfuse" not in fake_litellm.success_callback
        assert "langfuse" not in fake_litellm.failure_callback

    def test_installs_handler(self, fake_litellm):
        from shipit_watcher.instrumentation import litellm as inst

        inst.instrument()
        assert any(isinstance(c, inst.WatcherLiteLLMHandler)
                   for c in fake_litellm.callbacks)

    def test_idempotent(self, fake_litellm):
        from shipit_watcher.instrumentation import litellm as inst

        inst.instrument()
        inst.instrument()
        handlers = [c for c in fake_litellm.callbacks
                    if isinstance(c, inst.WatcherLiteLLMHandler)]
        assert len(handlers) == 1

    def test_can_keep_native_callback(self, fake_litellm):
        from shipit_watcher.instrumentation import litellm as inst

        inst.instrument(replace_langfuse_callback=False)
        assert "langfuse" in fake_litellm.success_callback

    def test_uninstrument_restores(self, fake_litellm):
        from shipit_watcher.instrumentation import litellm as inst

        inst.instrument()
        inst.uninstrument()
        assert fake_litellm.success_callback == ["langfuse"]
        assert not any(isinstance(c, inst.WatcherLiteLLMHandler)
                       for c in fake_litellm.callbacks)
        assert inst.is_instrumented() is False

    def test_uninstrument_without_instrument_is_noop(self, fake_litellm):
        from shipit_watcher.instrumentation import litellm as inst

        inst.uninstrument()  # must not raise

    def test_missing_litellm_returns_false(self, monkeypatch):
        from shipit_watcher.instrumentation import litellm as inst

        inst._instrumented = False
        monkeypatch.setitem(sys.modules, "litellm", None)
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

        def fake_import(name, *a, **k):
            if name == "litellm":
                raise ImportError("no litellm")
            return real_import(name, *a, **k)

        monkeypatch.setattr("builtins.__import__", fake_import)
        assert inst.instrument() is False

    def test_handler_records_into_ambient_trace(self, fake_litellm):
        from shipit_watcher.instrumentation import litellm as inst
        from shipit_watcher.tracer import Tracer
        import shipit_watcher.tracer as tmod

        captured = []

        class Sink:
            def start_trace(self, *a, **k): pass
            def end_trace(self, *a, **k): pass
            def record(self, event, ctx): captured.append(event)
            def flush(self): pass

        tracer = Tracer(sinks=[Sink()])
        previous, tmod._tracer = tmod._tracer, tracer
        try:
            handler = inst.WatcherLiteLLMHandler()
            response = types.SimpleNamespace(
                usage={"prompt_tokens": 10, "completion_tokens": 20}
            )
            with tracer.trace("req", company_id="acme"):
                handler.log_success_event(
                    {"model": "gpt-4o", "response_cost": 0.01},
                    response, 1.0, 2.0,
                )
        finally:
            tmod._tracer = previous

        assert len(captured) == 1
        assert captured[0].model == "gpt-4o"
        assert captured[0].total_tokens == 30

    def test_call_outside_a_trace_gets_its_own_named_trace(self, fake_litellm):
        """Background work — reindexing, a nightly report — has no ambient
        trace. Dropping it made instrumentation *lose* work; it gets a trace
        of its own instead, named `rag.embedding` rather than
        `litellm-aembedding`."""
        from shipit_watcher.instrumentation import litellm as inst
        from shipit_watcher.tracer import Tracer
        import shipit_watcher.tracer as tmod

        captured = []

        class Sink:
            def start_trace(self, *a, **k): pass
            def end_trace(self, *a, **k): pass
            def record(self, event, ctx): captured.append(event)
            def flush(self): pass

        previous, tmod._tracer = tmod._tracer, Tracer(sinks=[Sink()])
        try:
            inst.WatcherLiteLLMHandler().log_success_event(
                {"model": "text-embedding-3-small", "call_type": "aembedding"},
                types.SimpleNamespace(usage={}), 1.0, 2.0,
            )
        finally:
            tmod._tracer = previous
        assert [e.name for e in captured] == ["rag.embedding"]

    def test_call_type_names_the_operation(self):
        """The whole point: `litellm-aembedding` says how the bytes travelled,
        `rag.embedding` says what the system was doing."""
        from shipit_watcher.instrumentation.litellm import _call_name

        assert _call_name({"call_type": "aembedding"}) == "rag.embedding"
        assert _call_name({"call_type": "acompletion"}) == "llm.completion"
        assert _call_name({}) == "llm.completion"

    def test_explicit_generation_name_wins(self):
        from shipit_watcher.instrumentation.litellm import _call_name

        assert _call_name({
            "call_type": "aembedding",
            "metadata": {"generation_name": "rag.tickets"},
        }) == "rag.tickets"

    def test_handler_records_failures(self, fake_litellm):
        from shipit_watcher.instrumentation import litellm as inst
        from shipit_watcher.tracer import Tracer
        import shipit_watcher.tracer as tmod

        captured = []

        class Sink:
            def start_trace(self, *a, **k): pass
            def end_trace(self, *a, **k): pass
            def record(self, event, ctx): captured.append(event)
            def flush(self): pass

        tracer = Tracer(sinks=[Sink()])
        previous, tmod._tracer = tmod._tracer, tracer
        try:
            with tracer.trace("req"):
                inst.WatcherLiteLLMHandler().log_failure_event(
                    {"model": "gpt-4o", "exception": ValueError("rate limit")},
                    None, 1.0, 2.0,
                )
        finally:
            tmod._tracer = previous
        assert captured[0].severity == Severity.ERROR
        assert "rate limit" in captured[0].status_message

    def test_handler_never_raises(self, fake_litellm):
        from shipit_watcher.instrumentation import litellm as inst

        # Garbage in every position — the handler must absorb it.
        inst.WatcherLiteLLMHandler().log_success_event(None, None, None, None)

    def test_usage_from_object_shape(self, fake_litellm):
        from shipit_watcher.instrumentation.litellm import _usage_from

        response = types.SimpleNamespace(
            usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=6)
        )
        assert _usage_from(response) == (5, 6)

    def test_cost_falls_back_to_completion_cost(self, fake_litellm):
        from shipit_watcher.instrumentation.litellm import _cost_from

        assert _cost_from({}, types.SimpleNamespace()) == 0.005


class TestAmbientPrompt:
    """`wt.use_prompt(...)` attributes calls that cannot pass metadata."""

    def test_ambient_prompt_reaches_a_generation(self):
        import shipit_watcher as wt
        from shipit_watcher.tracer import Tracer
        import shipit_watcher.tracer as tmod

        class Collector:
            def __init__(self): self.events = []
            def start_trace(self, *a, **k): pass
            def end_trace(self, *a, **k): pass
            def record(self, event, ctx): self.events.append(event)
            def flush(self): pass

        sink = Collector()
        previous, tmod._tracer = tmod._tracer, Tracer(sinks=[sink])
        try:
            identity = wt.identify_prompt("You are a fleet assistant.",
                                          name="fleet-assistant", version="2",
                                          registered=True)
            with tmod._tracer.trace("turn"):
                with wt.use_prompt(identity):
                    with tmod._tracer.generation("llm.x", model="m"):
                        pass
        finally:
            tmod._tracer = previous

        assert sink.events[0].prompt["prompt_name"] == "fleet-assistant"
        assert sink.events[0].prompt["prompt_version"] == "2"

    def test_explicit_prompt_wins_over_ambient(self):
        import shipit_watcher as wt
        from shipit_watcher.tracer import Tracer
        import shipit_watcher.tracer as tmod

        class Collector:
            def __init__(self): self.events = []
            def start_trace(self, *a, **k): pass
            def end_trace(self, *a, **k): pass
            def record(self, event, ctx): self.events.append(event)
            def flush(self): pass

        sink = Collector()
        previous, tmod._tracer = tmod._tracer, Tracer(sinks=[sink])
        try:
            ambient = wt.identify_prompt("a", name="ambient")
            explicit = wt.identify_prompt("b", name="explicit")
            with tmod._tracer.trace("turn"):
                with wt.use_prompt(ambient):
                    with tmod._tracer.generation("llm.x", model="m", prompt=explicit):
                        pass
        finally:
            tmod._tracer = previous

        assert sink.events[0].prompt["prompt_name"] == "explicit"

    def test_binding_does_not_leak_past_the_block(self):
        import shipit_watcher as wt
        from shipit_watcher.context import current_context

        with wt.use_prompt(wt.identify_prompt("x", name="p")):
            assert current_context().prompt
        assert not current_context().prompt

    def test_accepts_a_managed_prompt(self):
        import shipit_watcher as wt
        from shipit_watcher.context import current_context
        from shipit_watcher.prompts import ManagedPrompt

        managed = ManagedPrompt(name="fleet-assistant", template="hi", version="3")
        with wt.use_prompt(managed):
            assert current_context().prompt["prompt_name"] == "fleet-assistant"
