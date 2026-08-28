"""LangSmith OTLP trace and feedback backend."""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_config
from ..context import TraceContext
from ..events import Event, EventType, GenerationEvent
from .langfuse_otel_sink import _attr
from .openinference_otel_sink import OpenInferenceOTLPSink

logger = logging.getLogger(__name__)


class LangSmithOTLPSink(OpenInferenceOTLPSink):
    backend_name = "langsmith"

    def __init__(self, endpoint: str | None = None, timeout: float = 5.0):
        config = get_config()
        collector = endpoint or config.langsmith_otel_endpoint
        if not collector:
            collector = f"{config.langsmith_endpoint.rstrip('/')}/otel"
        headers = {
            "x-api-key": config.langsmith_api_key,
            "Langsmith-Project": config.langsmith_project,
        } if config.langsmith_api_key else {}
        super().__init__(collector, headers=headers,
                         project_name=config.langsmith_project, timeout=timeout)

    @property
    def available(self) -> bool:
        return bool(self._endpoint and get_config().langsmith_api_key)

    def _root_attributes(
        self,
        context: TraceContext,
        root: dict[str, Any],
        output: Any,
        metadata: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        combined = context.merged_metadata(metadata or {})
        attributes = [
            _attr("langsmith.span.kind", "chain"),
            _attr("langsmith.trace.name", root["name"]),
        ]
        session = (
            context.session_id
            or combined.get("thread_id")
            or combined.get("conversation_id")
            or combined.get("experiment_id")
            or combined.get("run_name")
        )
        if session:
            attributes.append(_attr("langsmith.trace.session_id", session))
        if context.tags:
            attributes.append(_attr("langsmith.span.tags", ",".join(context.tags)))
        reference = combined.get("reference_example_id") or combined.get("item_id")
        if reference:
            attributes.append(_attr("langsmith.reference_example_id", reference))
        return attributes

    def _event_attributes(
        self, event: Event, context: TraceContext
    ) -> list[dict[str, Any]]:
        kinds = {
            EventType.AGENT: "chain",
            EventType.CHAIN: "chain",
            EventType.GENERATION: "llm",
            EventType.TOOL_INVOCATION: "tool",
            EventType.RETRIEVAL: "retriever",
        }
        attributes = [_attr("langsmith.span.kind", kinds.get(event.type, "chain"))]
        if isinstance(event, GenerationEvent):
            attributes.extend([
                _attr("langsmith.metadata.ls_provider", event.provider or "unknown"),
                _attr("langsmith.metadata.ls_model_name", event.model),
            ])
        return attributes

    def record_score(self, score: Any) -> None:
        try:
            from langsmith import Client

            payload: dict[str, Any] = {
                "key": score.name,
                "comment": score.comment or None,
                "trace_id": score.trace_id,
                "run_id": score.observation_id,
            }
            if score.numeric_value is not None:
                payload["score"] = score.numeric_value
            else:
                payload["value"] = score.value
            Client().create_feedback(**{k: v for k, v in payload.items() if v is not None})
        except Exception:
            logger.warning("watcher: langsmith feedback failed", exc_info=True)
