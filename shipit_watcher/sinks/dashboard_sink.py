"""Ship canonical, privacy-sanitised trace bundles to Watcher's own UI.

Delivery is ordered on purpose: a score references a trace, so the trace row
has to exist first. One worker draining one FIFO queue is what guarantees
that, which is why this sink queues the bundle and its scores together rather
than sending each as it is produced.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..config import get_config
from ..replay import TraceBundleSink
from .delivery import DeliveryError, DeliveryWorker, explain_http_error

logger = logging.getLogger(__name__)


class DashboardSink(TraceBundleSink):
    backend_name = "watcher-dashboard"

    def __init__(self, url: str | None = None, token: str | None = None):
        super().__init__()
        config = get_config()
        self._base_url = (url or config.dashboard_url).rstrip("/")
        self._token = token if token is not None else config.dashboard_token
        self._lock = threading.Lock()
        self._pending_scores: dict[str, list[dict[str, Any]]] = {}
        self._delivery = DeliveryWorker(
            lambda item: self._send(*item),
            size=config.exporter_queue_size,
            attempts=config.exporter_max_retries,
        )

    @property
    def available(self) -> bool:
        return bool(self._base_url)

    def end_trace(
        self,
        trace_id: str,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().end_trace(trace_id, output, metadata)
        with self._lock:
            bundle = self.bundles.pop(trace_id, None)
            # Popped unconditionally, even when there is no bundle to send.
            # Draining these only on the success branch stranded every score
            # queued against a trace that never produced one.
            scores = self._pending_scores.pop(trace_id, [])
        if not self.available:
            return
        if bundle is not None:
            self._delivery.submit(("/api/ingest", bundle.to_dict()))
        for score in scores:
            self._delivery.submit(("/api/scores", score))

    def record_score(self, score: Any) -> None:
        if not self.available or not score.trace_id:
            return
        payload = score.to_dict()
        # The check and the queue-up happen under one lock. Evaluators and
        # LiteLLM callbacks record scores from their own threads while the
        # request thread closes the trace — unguarded, a score could observe
        # a live bundle, then be appended to a list `end_trace` had already
        # drained, and never be sent at all.
        with self._lock:
            if score.trace_id in self.bundles:
                self._pending_scores.setdefault(score.trace_id, []).append(payload)
                return
        self._delivery.submit(("/api/scores", payload))

    def _send(self, path: str, payload: dict[str, Any]) -> None:
        import json
        import urllib.error
        import urllib.request

        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload, default=str).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                if response.status >= 300:
                    raise DeliveryError(
                        explain_http_error(response.status, "the Watcher dashboard")
                    )
        except urllib.error.HTTPError as error:
            raise DeliveryError(
                explain_http_error(error.code, "the Watcher dashboard",
                                   error.read().decode("utf-8", "replace")),
            ) from error
        except urllib.error.URLError as error:
            raise DeliveryError(
                f"could not reach the Watcher dashboard at {self._base_url}: "
                f"{error.reason}. Is it running, and is WATCHER_DASHBOARD_URL right?",
            ) from error

    def flush(self) -> None:
        self._delivery.flush()

    @property
    def delivery_stats(self) -> dict[str, int]:
        return {
            "delivered": self._delivery.delivered,
            "failed": self._delivery.failed,
            "dropped": self._delivery.dropped,
        }
