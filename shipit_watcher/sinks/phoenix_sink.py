"""Phoenix trace and annotation backend."""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_config
from .openinference_otel_sink import OpenInferenceOTLPSink

logger = logging.getLogger(__name__)


class PhoenixOTLPSink(OpenInferenceOTLPSink):
    backend_name = "phoenix"

    def __init__(self, endpoint: str | None = None, timeout: float = 5.0):
        config = get_config()
        collector = endpoint or config.phoenix_collector_endpoint
        if not collector and config.phoenix_base_url:
            collector = config.phoenix_base_url
        headers: dict[str, str] = {}
        if config.phoenix_api_key:
            headers["Authorization"] = f"Bearer {config.phoenix_api_key}"
        if config.phoenix_project:
            headers["x-project-name"] = config.phoenix_project
        super().__init__(collector, headers=headers,
                         project_name=config.phoenix_project, timeout=timeout)

    def record_score(self, score: Any) -> None:
        try:
            from phoenix.client import Client

            result: dict[str, Any] = {"label": str(score.value)}
            if score.numeric_value is not None:
                result["score"] = score.numeric_value
            annotation: Any = {
                "name": score.name,
                "span_id": score.observation_id or score.trace_id,
                "annotator_kind": score.source.value.upper(),
                "result": result,
                "metadata": score.metadata,
            }
            Client().spans.log_span_annotations(span_annotations=[annotation])
        except Exception:
            logger.warning("watcher: phoenix score failed", exc_info=True)
