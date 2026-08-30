"""Export delivery: bounded, retried, fork-safe, and never on the request path."""

from __future__ import annotations

import threading

import pytest

from shipit_watcher.config import configure, reset_config
from shipit_watcher.scoring import Score
from shipit_watcher.sinks.dashboard_sink import DashboardSink
from shipit_watcher.sinks.delivery import DeliveryWorker
from shipit_watcher.sinks.langfuse_otel_sink import LangfuseOTLPSink
from shipit_watcher.tracer import Tracer


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


def test_a_transient_failure_is_retried_not_dropped():
    """A 503 is usually a collector restart, and the trace is unreconstructable."""
    attempts: list[int] = []
    done = threading.Event()

    def flaky(value):
        attempts.append(value)
        if len(attempts) < 3:
            raise RuntimeError("collector restarting")
        done.set()

    worker = DeliveryWorker(flaky, size=8, attempts=3)
    worker.submit("spans")
    assert done.wait(timeout=5.0)
    assert len(attempts) == 3
    assert worker.delivered == 1
    assert worker.failed == 0


def test_a_full_queue_drops_rather_than_blocking_the_caller():
    """The request thread must never wait on a backlog."""
    release = threading.Event()
    worker = DeliveryWorker(lambda value: release.wait(timeout=5.0), size=1, attempts=1)
    for index in range(50):
        worker.submit(index)          # must not block
    release.set()
    assert worker.dropped > 0


def test_flush_returns_promptly_when_no_worker_is_alive():
    """A forked child inherits the queue but not the thread that drains it.

    Waiting on `unfinished_tasks` there waits for something that cannot
    happen — once per sink, at every prefork worker's shutdown.
    """
    import time

    worker = DeliveryWorker(lambda value: None, size=8, attempts=1)
    worker._queue.put_nowait("orphaned")      # queued, no consumer ever started
    started = time.monotonic()
    worker.flush(timeout=10.0)
    assert time.monotonic() - started < 1.0


def test_langfuse_export_does_not_block_the_request_thread():
    """The default transport used to POST synchronously from end_trace."""
    import time

    configure(langfuse_public_key="pk", langfuse_secret_key="sk",
              langfuse_host="https://langfuse.example", exporter_async=True)
    sink = LangfuseOTLPSink()
    sink._transmit = lambda spans: time.sleep(1.5)          # a lagging collector
    tracer = Tracer(sinks=[sink])

    started = time.monotonic()
    with tracer.trace("request"):
        pass
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, f"end_trace blocked for {elapsed:.2f}s"


def test_a_score_is_still_delivered_when_its_trace_produced_no_bundle():
    """Draining pending scores only on the success branch stranded them.

    A trace closed twice — the second close finds no bundle — used to leave
    anything queued against it in `_pending_scores` for the life of the
    process, never sent and never surfaced.
    """
    sent: list[tuple[str, dict]] = []
    sink = DashboardSink(url="https://watcher.example")
    sink._send = lambda path, payload: sent.append((path, payload))

    sink._pending_scores["abc"] = [Score("quality", 0.9, trace_id="abc").to_dict()]
    sink.end_trace("abc")                 # no bundle under that id
    sink.flush()

    assert [path for path, _ in sent] == ["/api/scores"]
    assert sink._pending_scores == {}


def test_a_score_recorded_mid_trace_lands_after_the_trace_row():
    """Ordering is the point: a score references a trace that must exist."""
    sent: list[tuple[str, dict]] = []
    sink = DashboardSink(url="https://watcher.example")
    sink._send = lambda path, payload: sent.append((path, payload))
    tracer = Tracer(sinks=[sink])

    with tracer.trace("request") as context:
        sink.record_score(Score("quality", 0.9, trace_id=context.trace_id))
        assert sent == []                 # held until the trace is delivered
    sink.flush()

    assert [path for path, _ in sent] == ["/api/ingest", "/api/scores"]
