"""Traces that end badly — cancelled, abandoned, sampled out, nested.

Every case here was a way to lose a trace entirely, and the requests worth
investigating are exactly the ones that were disappearing.
"""

from __future__ import annotations

import asyncio

import pytest

import shipit_watcher as wt
from shipit_watcher.config import configure, reset_config
from shipit_watcher.events import Event
from shipit_watcher.tracer import Tracer


class Collector:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.ended: list[str] = []
        self.events: list[str] = []

    def start_trace(self, trace_id, name, context, input_data=None) -> None:
        self.started.append(name)

    def end_trace(self, trace_id, output=None, metadata=None) -> None:
        self.ended.append(trace_id)

    def record(self, event, context) -> None:
        self.events.append(event.name)

    def flush(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


def test_cancelled_request_still_ends_its_trace():
    """A client disconnect arrives as CancelledError — a BaseException.

    Caught only as `Exception`, it skipped both the error and the success
    path: started, never ended, never exported.
    """
    configure(sample_rate=1.0)
    sink = Collector()
    tracer = Tracer(sinks=[sink])

    async def scenario():
        async def handler():
            with tracer.trace("request"):
                await asyncio.sleep(10)

        task = asyncio.ensure_future(handler())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert sink.started == ["request"]
    assert len(sink.ended) == 1


def test_abandoned_stream_still_ends_its_trace():
    """An SSE response abandoned mid-stream closes the generator.

    That throws GeneratorExit into the yield — also a BaseException.
    """
    configure(sample_rate=1.0)
    sink = Collector()
    tracer = Tracer(sinks=[sink])

    def stream():
        with tracer.trace("stream"):
            yield "first"
            yield "second"

    generator = stream()
    next(generator)
    generator.close()

    assert sink.started == ["stream"]
    assert len(sink.ended) == 1


def test_abandoned_span_is_still_recorded():
    configure(sample_rate=1.0)
    sink = Collector()
    tracer = Tracer(sinks=[sink])

    def stream():
        with tracer.trace("turn"), tracer.span("work"):
            yield "first"
            yield "second"

    generator = stream()
    next(generator)
    generator.close()

    assert sink.events == ["work"]


def test_nested_trace_does_not_inherit_being_sampled_out():
    """A sampled-in trace opened inside a sampled-out one must export.

    `bind` copies the parent's fields, so without an explicit `sampled=True`
    the inner trace exported a root span with no children at all.
    """
    configure(sample_rate=0.0)
    sink = Collector()
    tracer = Tracer(sinks=[sink])

    with tracer.trace("outer"):
        configure(sample_rate=1.0)
        with tracer.trace("inner"), tracer.tool("lookup"):
            pass

    assert sink.events == ["tool.lookup"]


def test_late_event_does_not_resurrect_a_closed_tail_buffer():
    """LiteLLM's streaming callback fires after the trace closed.

    `setdefault` re-created a buffer nothing would ever pop — one permanent
    entry per sampled-out streaming request.
    """
    configure(sample_rate=0.0)
    tracer = Tracer(sinks=[Collector()])

    with tracer.trace("background") as context:
        captured = context

    tracer._emit(Event(name="late.callback"), captured)
    assert tracer._tail_buffers == {}


def test_live_tail_buffers_are_bounded():
    """`tail_buffer_max_events` bounds one trace; this bounds the fleet."""
    configure(sample_rate=0.0, tail_buffer_max_traces=10)
    tracer = Tracer(sinks=[Collector()])

    held = []
    for index in range(50):
        manager = tracer.trace(f"unfinished-{index}")
        manager.__enter__()
        held.append(manager)   # keep them open: no GC, no close

    assert len(tracer._tail_buffers) == 10


def test_budget_can_be_carried_to_a_worker_thread():
    """A plain thread starts with an empty context, so the budget is invisible.

    That is where a hard ceiling matters most — parallel tool fan-out.
    """
    from concurrent.futures import ThreadPoolExecutor

    with wt.budget(0.10, action="block") as state:
        def work():
            with wt.use_budget(state):
                return wt.current_budget() is state

        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(work).result() is True


def test_concurrent_charges_are_not_lost():
    from concurrent.futures import ThreadPoolExecutor

    with wt.budget(100.0, action="warn") as state, ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: state.charge(0.01), range(400)))

    assert round(state.spent_usd, 6) == 4.0
