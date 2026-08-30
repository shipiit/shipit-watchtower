"""Vendor-neutral OTLP/HTTP exporter using OpenInference and GenAI semantics."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import urllib.error
import urllib.request
from typing import Any

from ..config import get_config
from ..context import TraceContext
from ..events import Event, EventType, GenerationEvent
from .delivery import DeliveryError, DeliveryWorker, explain_http_error
from .langfuse_otel_sink import _attr, _hex_id, _now_ns

#: Retained under its historical name for anything importing it directly.
_DeliveryWorker = DeliveryWorker

logger = logging.getLogger(__name__)


_KINDS = {
    EventType.AGENT: "AGENT",
    EventType.CHAIN: "CHAIN",
    EventType.GENERATION: "LLM",
    EventType.TOOL_INVOCATION: "TOOL",
    EventType.RETRIEVAL: "RETRIEVER",
    EventType.HANDOFF: "AGENT",
    EventType.DECISION: "CHAIN",
    EventType.POLICY: "CHAIN",
    EventType.HUMAN_REVIEW: "CHAIN",
    EventType.VALIDATION: "CHAIN",
    EventType.SPAN: "CHAIN",
}


def _trace_endpoint(value: str) -> str:
    endpoint = (value or "").rstrip("/")
    if not endpoint or endpoint.endswith("/v1/traces"):
        return endpoint
    return f"{endpoint}/v1/traces"


class OpenInferenceOTLPSink:
    """Buffers one trace and exports it as a valid OTLP span tree."""

    backend_name = "otlp"

    def __init__(self, endpoint: str, *, headers: dict[str, str] | None = None,
                 project_name: str = "", timeout: float = 5.0,
                 background: bool | None = None):
        self._endpoint = _trace_endpoint(endpoint)
        self._headers = dict(headers or {})
        self._project = project_name
        self._timeout = timeout
        self._lock = threading.Lock()
        self._roots: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, list[dict[str, Any]]] = {}
        config = get_config()
        use_background = config.exporter_async if background is None else background
        self._delivery = DeliveryWorker(
            # Late-bound on purpose: passing the bound method here would
            # freeze it at construction, so a subclass override — or a test
            # substituting the transport — would be silently ignored while
            # the real one kept firing.
            lambda spans: self._transmit(spans),
            size=config.exporter_queue_size,
            attempts=config.exporter_max_retries,
        ) if use_background else None

    @property
    def available(self) -> bool:
        return bool(self._endpoint)

    def start_trace(self, trace_id: str, name: str, context: TraceContext,
                    input_data: Any = None) -> None:
        if not self.available:
            return
        with self._lock:
            self._roots[trace_id] = {
                "name": name,
                "context": context,
                "input": input_data,
                "span_id": context.root_span_id or secrets.token_hex(8),
                "started": _now_ns(),
            }
            self._pending.setdefault(trace_id, [])

    def record(self, event: Event, context: TraceContext) -> None:
        if not self.available or not context.trace_id:
            return
        with self._lock:
            root = self._roots.get(context.trace_id)
            self._pending.setdefault(context.trace_id, []).append(
                self._span(event, context, root["span_id"] if root else None)
            )

    def end_trace(self, trace_id: str, output: Any = None,
                  metadata: dict[str, Any] | None = None) -> None:
        if not self.available:
            return
        with self._lock:
            root = self._roots.pop(trace_id, None)
            children = self._pending.pop(trace_id, [])
        if root is None:
            return
        spans = [self._root(trace_id, root, output, metadata, children), *children]
        self._send(spans)

    def _root(self, trace_id: str, root: dict[str, Any], output: Any,
              metadata: dict[str, Any] | None,
              children: list[dict[str, Any]]) -> dict[str, Any]:
        context: TraceContext = root["context"]
        attributes = [
            _attr("openinference.span.kind", "AGENT"),
            _attr("input.value", root["input"]),
            _attr("input.mime_type", "application/json"),
        ]
        if output is not None:
            attributes.extend([
                _attr("output.value", output),
                _attr("output.mime_type", "application/json"),
            ])
        if context.session_id:
            attributes.append(_attr("session.id", str(context.session_id)))
        if context.user_id:
            attributes.append(_attr("user.id", str(context.user_id)))
        combined = context.merged_metadata(metadata or {})
        combined.update({
            "company_id": context.company_id,
            "cost_center": context.cost_center,
            "channel": context.channel,
            "tags": context.tags,
        })
        attributes.append(_attr("metadata", {k: v for k, v in combined.items()
                                              if v not in (None, "", [])}))
        attributes.extend(self._root_attributes(context, root, output, metadata))
        starts = [int(s["startTimeUnixNano"]) for s in children]
        ends = [int(s["endTimeUnixNano"]) for s in children]
        span = {
            "traceId": _hex_id(trace_id, 32),
            "spanId": _hex_id(root["span_id"], 16),
            "name": root["name"],
            "kind": 1,
            "startTimeUnixNano": str(min(starts) if starts else root["started"]),
            "endTimeUnixNano": str(max(ends) if ends else _now_ns()),
            "attributes": attributes,
        }
        if context.remote_parent_id:
            span["parentSpanId"] = _hex_id(context.remote_parent_id, 16)
        return span

    def _span(self, event: Event, context: TraceContext,
              root_span_id: str | None) -> dict[str, Any]:
        attributes = [
            _attr("openinference.span.kind", _KINDS.get(event.type, "CHAIN")),
            _attr("input.value", event.input),
            _attr("input.mime_type", "application/json"),
            _attr("metadata", event.to_payload()),
        ]
        if event.output is not None:
            attributes.extend([
                _attr("output.value", event.output),
                _attr("output.mime_type", "application/json"),
            ])
        if isinstance(event, GenerationEvent):
            attributes.extend([
                _attr("gen_ai.operation.name", "chat"),
                _attr("gen_ai.request.model", event.model),
                _attr("gen_ai.system", event.provider or "unknown"),
                _attr("gen_ai.usage.input_tokens", event.prompt_tokens),
                _attr("gen_ai.usage.output_tokens", event.completion_tokens),
                _attr("llm.model_name", event.model),
                _attr("llm.token_count.prompt", event.prompt_tokens),
                _attr("llm.token_count.completion", event.completion_tokens),
                _attr("llm.token_count.total", event.total_tokens),
            ])
            if event.total_cost:
                attributes.append(_attr("llm.cost.total", event.total_cost))
        attributes.extend(self._event_attributes(event, context))
        parent = event.parent_id or root_span_id
        span = {
            "traceId": _hex_id(context.trace_id, 32),
            "spanId": _hex_id(event.id, 16),
            "name": event.name,
            "kind": 1,
            "startTimeUnixNano": str(int(event.started_at * 1e9)),
            "endTimeUnixNano": str(int((event.ended_at or event.started_at) * 1e9)),
            "attributes": attributes,
            "status": {"code": 2 if event.severity.value == "ERROR" else 0},
        }
        if parent:
            span["parentSpanId"] = _hex_id(parent, 16)
        return span

    def _root_attributes(
        self,
        context: TraceContext,
        root: dict[str, Any],
        output: Any,
        metadata: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Vendor extension point while retaining one canonical mapping."""
        return []

    def _event_attributes(
        self, event: Event, context: TraceContext
    ) -> list[dict[str, Any]]:
        return []

    def _send(self, spans: list[dict[str, Any]]) -> None:
        if self._delivery is not None:
            self._delivery.submit(spans)
            return
        try:
            self._transmit(spans)
        except Exception:
            logger.warning("watcher: %s export failed", self.backend_name, exc_info=True)

    def _transmit(self, spans: list[dict[str, Any]]) -> None:
        config = get_config()
        resource = [
            _attr("service.name", config.service_name),
            _attr("service.version", config.release or "unknown"),
            _attr("deployment.environment.name", config.environment),
        ]
        if self._project:
            resource.append(_attr("openinference.project.name", self._project))
        payload = {"resourceSpans": [{
            "resource": {"attributes": resource},
            "scopeSpans": [{"scope": {"name": "shipit_watcher"}, "spans": spans}],
        }]}
        headers = {"Content-Type": "application/json", **self._headers}
        request = urllib.request.Request(
            self._endpoint, data=json.dumps(payload, default=str).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                if response.status >= 300:
                    raise DeliveryError(
                        explain_http_error(response.status, self.backend_name)
                    )
        except urllib.error.HTTPError as error:
            raise DeliveryError(
                explain_http_error(error.code, self.backend_name,
                                   error.read().decode("utf-8", "replace")),
            ) from error
        except urllib.error.URLError as error:
            raise DeliveryError(
                f"could not reach {self.backend_name} at {self._endpoint}: {error.reason}",
            ) from error

    def flush(self) -> None:
        if self._delivery is not None:
            self._delivery.flush()

    @property
    def delivery_stats(self) -> dict[str, int]:
        if self._delivery is None:
            return {"delivered": 0, "failed": 0, "dropped": 0}
        return {
            "delivered": self._delivery.delivered,
            "failed": self._delivery.failed,
            "dropped": self._delivery.dropped,
        }
