"""Background delivery for HTTP exporters.

Sinks sit on the request path, so an export has to leave it: a collector that
is merely slow must not become the application's latency, and one that is
briefly unreachable must not lose the trace. The queue is bounded (a backlog
is dropped rather than growing without limit), delivery is retried with
jittered backoff, and the worker is re-created after a fork so a prefork
server's children do not inherit a thread that no longer exists.
"""

from __future__ import annotations

import logging
import os
import queue
import random
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DeliveryError", "DeliveryWorker", "explain_http_error"]


class DeliveryError(RuntimeError):
    """A delivery failure with an explanation a person can act on.

    Raised instead of letting a transport exception escape, because what
    surfaced before was a urllib traceback ending in ``HTTPError: 401`` —
    which says an export failed, but not that the token is wrong, and
    certainly not which of the two sides to change.
    """


def explain_http_error(status: int, backend: str, body: str = "") -> str:
    """Turn a status code into the sentence the reader actually needs."""
    detail = f" ({body.strip()[:120]})" if body.strip() else ""
    if status in (401, 403):
        return (
            f"{backend} rejected the trace: not authorised{detail}. "
            f"Check the credentials on both sides match — for the Watcher "
            f"dashboard that is WATCHER_DASHBOARD_TOKEN against the "
            f"dashboard's own WATCHER_INGEST_KEY."
        )
    if status == 404:
        return (
            f"{backend} returned 404{detail}. The host is reachable but the "
            f"ingest path is not there — check the URL points at the service "
            f"root, not at a page."
        )
    if status == 413:
        return f"{backend} refused the trace as too large{detail}."
    if status == 429:
        return f"{backend} is rate limiting; the trace will be retried{detail}."
    if 500 <= status < 600:
        return f"{backend} failed with HTTP {status}{detail}; retrying."
    return f"{backend} returned HTTP {status}{detail}."


class DeliveryWorker:
    """Bounded, retrying and fork-safe background delivery."""

    def __init__(self, send: Any, *, size: int, attempts: int):
        self._send = send
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max(1, size))
        self._attempts = max(1, attempts)
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._reported: set[str] = set()
        self._pid = os.getpid()
        self.delivered = 0
        self.dropped = 0
        self.failed = 0

    def _ensure_thread(self) -> None:
        if self._pid != os.getpid():
            # A forked child inherits the parent's queue contents but not its
            # threads. Anything queued at fork time belongs to the parent and
            # would be exported twice; the counters are the parent's too.
            self._queue = queue.Queue(maxsize=self._queue.maxsize)
            self._thread = None
            self._pid = os.getpid()
            self.delivered = self.dropped = self.failed = 0
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, name="watcher-otlp", daemon=True
            )
            self._thread.start()

    def submit(self, value: Any) -> None:
        with self._start_lock:
            # Check-then-act, guarded: two threads submitting at once could
            # otherwise both see a dead worker and start one each, putting two
            # consumers on one FIFO and losing the ordering the dashboard sink
            # depends on.
            self._ensure_thread()
        try:
            self._queue.put_nowait(value)
        except queue.Full:
            self.dropped += 1
            logger.warning("watcher: OTLP delivery queue full; trace dropped")

    def _run(self) -> None:
        while True:
            value = self._queue.get()
            try:
                for attempt in range(self._attempts):
                    try:
                        self._send(value)
                        self.delivered += 1
                        break
                    except DeliveryError as error:
                        if attempt + 1 == self._attempts:
                            self.failed += 1
                            # No traceback: the message already says what to
                            # do, and a stack through urllib buries it.
                            self._warn_once(str(error))
                    except Exception:
                        if attempt + 1 == self._attempts:
                            self.failed += 1
                            logger.warning(
                                "watcher: export failed after retries", exc_info=True,
                            )
                        else:
                            delay = min(5.0, 0.2 * (2 ** attempt))
                            time.sleep(delay * random.uniform(0.75, 1.25))
            finally:
                self._queue.task_done()

    def _warn_once(self, message: str) -> None:
        """Say it once per distinct problem, not once per dropped trace.

        A misconfigured token fails on every request, and a log line per
        request buries the one line that explains it.
        """
        if message in self._reported:
            return
        self._reported.add(message)
        logger.warning("watcher: %s", message)

    def flush(self, timeout: float = 10.0) -> None:
        """Wait for the backlog to drain, but never longer than it can help.

        The pid check matters here as much as in ``submit``: a forked child
        inherits a queue whose consumer thread did not survive the fork, so
        waiting on ``unfinished_tasks`` waits for something that will never
        happen. With ``setup()`` registering this at exit, every prefork
        worker paid the full timeout on shutdown, once per sink.
        """
        if self._pid != os.getpid() or self._thread is None or not self._thread.is_alive():
            if self._queue.unfinished_tasks:
                logger.warning(
                    "watcher: %d queued export(s) dropped — no live delivery worker",
                    self._queue.unfinished_tasks,
                )
            return
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._queue.unfinished_tasks:
            logger.warning(
                "watcher: flush deadline reached with %d export(s) still queued",
                self._queue.unfinished_tasks,
            )
