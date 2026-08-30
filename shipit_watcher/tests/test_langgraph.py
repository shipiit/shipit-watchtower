from __future__ import annotations

import uuid
from types import SimpleNamespace

import shipit_watcher as wt
from shipit_watcher.events import EventType, GenerationEvent, Severity, ToolInvocationEvent
from shipit_watcher.instrumentation.langgraph import (
    WatcherLangGraphCallback,
    instrument_langgraph,
    langgraph_callback,
)
from shipit_watcher.tracer import Tracer


class RecordingSink:
    def __init__(self):
        self.events = []
        self.traces = []
        self.ended = []

    def start_trace(self, trace_id, name, context, input):
        self.traces.append((trace_id, name, context, input))

    def end_trace(self, trace_id, output, metadata):
        self.ended.append((trace_id, output, metadata))

    def record(self, event, context):
        self.events.append((event, context))

    def flush(self):
        return None


def test_callback_records_nested_graph_tool_and_generation():
    wt.configure(service_name="graph-test", environment="test")
    sink = RecordingSink()
    tracer = Tracer(sinks=[sink])
    callback = WatcherLangGraphCallback(tracer)
    with tracer.trace("graph.invoke", user_id="u-1"):
        callback.on_chain_start(
            {"name": "research"},
            {"topic": "AI"},
            run_id="node-1",
            metadata={"langgraph_node": "research"},
        )
        callback.on_tool_start(
            {"name": "search"},
            "AI",
            run_id="tool-1",
            parent_run_id="node-1",
        )
        callback.on_tool_end(["result"], run_id="tool-1")
        callback.on_llm_start(
            {"name": "answer"},
            ["prompt"],
            run_id="llm-1",
            parent_run_id="node-1",
            invocation_params={"model": "gpt-test"},
        )
        response = SimpleNamespace(
            llm_output={"token_usage": {"prompt_tokens": 4, "completion_tokens": 6}}
        )
        callback.on_llm_end(response, run_id="llm-1")
        callback.on_chain_end({"answer": "done"}, run_id="node-1")

    events = {event.id: event for event, _ in sink.events}
    assert events["node1"].type == EventType.AGENT
    assert isinstance(events["tool1"], ToolInvocationEvent)
    assert events["tool1"].parent_id == "node1"
    assert isinstance(events["llm1"], GenerationEvent)
    assert events["llm1"].total_tokens == 10
    assert {context.user_id for _, context in sink.events} == {"u-1"}


def test_callback_records_errors_without_raising():
    wt.configure(service_name="graph-test", environment="test")
    sink = RecordingSink()
    tracer = Tracer(sinks=[sink])
    callback = WatcherLangGraphCallback(tracer)
    with tracer.trace("graph.invoke"):
        callback.on_tool_start({"name": "fail"}, "x", run_id="tool-error")
        callback.on_tool_error(ValueError("bad tool"), run_id="tool-error")
    event = sink.events[0][0]
    assert event.severity == Severity.ERROR
    assert event.succeeded is False
    assert event.error == "bad tool"


def test_instrument_langgraph_uses_runnable_config():
    class Graph:
        def with_config(self, **config):
            self.config = config
            return self

    graph = Graph()
    assert instrument_langgraph(graph) is graph
    assert isinstance(graph.config["callbacks"][0], WatcherLangGraphCallback)


def test_callback_opens_standalone_trace_and_maps_graph_identity():
    wt.configure(service_name="graph-test", environment="test")
    sink = RecordingSink()
    callback = WatcherLangGraphCallback(Tracer(sinks=[sink]))

    callback.on_chain_start(
        {"name": "support-agent"},
        {"question": "Where is my order?"},
        run_id="root-run",
        metadata={
            "thread_id": "thread-42",
            "user_id": "user-7",
            "langgraph_step": 1,
        },
        tags=["production", "support"],
    )
    callback.on_tool_start(
        {"name": "lookup_order"},
        "A-123",
        run_id="tool-child",
        parent_run_id="root-run",
    )
    callback.on_tool_end({"status": "shipped"}, run_id="tool-child")
    callback.on_chain_end({"answer": "Shipped"}, run_id="root-run")

    assert len(sink.traces) == 1
    trace_id, name, context, input_data = sink.traces[0]
    assert name == "support-agent"
    assert context.session_id == "thread-42"
    assert context.user_id == "user-7"
    assert context.tags == ["production", "support"]
    assert context.metadata["langgraph_step"] == 1
    assert input_data == {"question": "Where is my order?"}
    assert {event.id for event, _ in sink.events} == {"rootrun", "toolchild"}
    assert {ctx.trace_id for _, ctx in sink.events} == {trace_id}
    assert sink.ended[0][1] == {"answer": "Shipped"}


def test_chat_stream_and_message_usage_are_captured():
    wt.configure(service_name="graph-test", environment="test")
    sink = RecordingSink()
    callback = WatcherLangGraphCallback(Tracer(sinks=[sink]))
    with callback.tracer.trace("chat"):
        callback.on_chat_model_start(
            {"name": "ChatOpenAI"},
            [[{"role": "user", "content": "hello"}]],
            run_id="chat-1",
            invocation_params={"model_name": "gpt-test"},
        )
        callback.on_llm_new_token("Hi", run_id="chat-1")
        message = SimpleNamespace(
            usage_metadata={"input_tokens": 8, "output_tokens": 3}
        )
        generation = SimpleNamespace(message=message)
        callback.on_llm_end(
            SimpleNamespace(llm_output={}, generations=[[generation]]),
            run_id="chat-1",
        )

    event = sink.events[0][0]
    assert event.input == [[{"role": "user", "content": "hello"}]]
    assert event.model == "gpt-test"
    assert event.total_tokens == 11
    assert event.metadata["streamed_chunks"] == 1
    assert event.metadata["first_token_latency_ms"] >= 0


def test_a_failed_graph_marks_its_root_trace_as_an_error():
    """Closing with (None, None, None) took the tracer's success path.

    The root then carried an error *payload* with an OK status, so anything
    alerting on trace status never saw the failure.
    """
    class StatusSink(RecordingSink):
        def __init__(self):
            super().__init__()
            self.ended: list[tuple[str, object]] = []

        def end_trace(self, trace_id, output=None, metadata=None):
            self.ended.append((trace_id, output))

    sink = StatusSink()
    tracer = Tracer(sinks=[sink])
    callback = langgraph_callback(tracer)

    run_id = uuid.uuid4()
    callback.on_chain_start({"name": "graph"}, {"q": "hi"}, run_id=run_id)
    callback.on_chain_error(RuntimeError("node blew up"), run_id=run_id)

    assert len(sink.ended) == 1
    assert "RuntimeError: node blew up" in str(sink.ended[0][1])
    assert callback._owned_traces == {}


def test_unfinished_runs_do_not_grow_without_bound():
    """An interrupted graph never fires its on_*_end hook."""
    callback = langgraph_callback(Tracer(sinks=[RecordingSink()]))
    callback.MAX_PENDING_RUNS = 16
    for _ in range(200):
        callback.on_tool_start({"name": "search"}, "query", run_id=uuid.uuid4())

    assert len(callback._runs) <= 16
