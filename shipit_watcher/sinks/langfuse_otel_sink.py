"""
Langfuse OTLP sink — the one that draws the agent graph.

Langfuse renders a graph for a trace only when its observations carry a
*semantic type*: ``AGENT``, ``TOOL``, ``RETRIEVER``, ``EMBEDDING``,
``GUARDRAIL``, ``CHAIN``, ``EVALUATOR``. A trace of undifferentiated ``SPAN``
rows renders as a list, because nothing in it says which box is an agent and
which is a tool it called.

Those types cannot be sent over the classic ingestion API. Asked to accept
one, a v3 server answers::

    "Invalid option: expected one of \\"GENERATION\\"|\\"SPAN\\"|\\"EVENT\\""

They are only reachable over OTLP, where the type is an OpenTelemetry span
attribute — ``langfuse.observation.type`` — rather than a field of the
ingestion schema.

The Langfuse Python SDK exposes this as ``as_type=`` from **3.3.1**. This
sink instead speaks OTLP directly, over plain HTTP with no OpenTelemetry
dependency, for a specific reason: the host application pins ``langfuse==2.60.10`` and
``langfuse-langchain==2.60.10.1``, and v3 removed ``client.trace()`` — the
call the application uses in a dozen places. Requiring the v3 SDK would make
"see the graph" a breaking dependency upgrade. Speaking the wire format keeps
the two decoupled: the *server* is what has to be v3, and it already is.

The classic sink stays the default. This one is opt-in::

    wt.configure(langfuse_transport="otlp")

or ``WATCHER_LANGFUSE_TRANSPORT=otlp``.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..config import get_config
from ..context import TraceContext
from ..events import Event, EventType, GenerationEvent, Severity

logger = logging.getLogger(__name__)

__all__ = ["LangfuseOTLPSink", "OBSERVATION_TYPES"]


#: Watcher event type → Langfuse observation type.
#:
#: This mapping is the whole feature. Langfuse infers the graph from these
#: names, so a retrieval recorded as a generic span is a node the graph cannot
#: draw. Types Langfuse does not know fall back to ``span``, which degrades to
#: today's behaviour rather than dropping the observation.
OBSERVATION_TYPES: Dict[EventType, str] = {
    EventType.GENERATION: "generation",
    EventType.TOOL_INVOCATION: "tool",
    EventType.RETRIEVAL: "retriever",
    EventType.HANDOFF: "agent",       # delegation — the receiving agent's work
    EventType.POLICY: "guardrail",
    EventType.VALIDATION: "evaluator",
    EventType.DECISION: "chain",      # a branch point composing further steps
    EventType.HUMAN_REVIEW: "span",   # a wait, not a computation
    EventType.SPAN: "span",
}

#: Severity → OTLP status code. UNSET rather than OK for the normal case:
#: OK means "explicitly asserted successful", which a span that merely did not
#: raise has not earned.
_STATUS_CODES = {
    Severity.ERROR: 2,      # STATUS_CODE_ERROR
    Severity.WARNING: 0,
    Severity.DEFAULT: 0,
    Severity.DEBUG: 0,
}

#: OTLP identifier widths, in hex characters.
_TRACE_ID_HEX = 32
_SPAN_ID_HEX = 16


def _hex_id(value: Optional[str], width: int) -> str:
    """Coerce an id to a valid OTLP identifier of ``width`` hex characters.

    Watcher ids are already 32-char hex, so this is normally a slice. Ids
    from elsewhere — a caller-supplied session key, a UUID with dashes — are
    normalised rather than rejected: dropping the span would lose the
    observation entirely, and OTLP will not accept a malformed id.
    """
    if not value:
        return secrets.token_hex(width // 2)
    cleaned = "".join(c for c in str(value).lower() if c in "0123456789abcdef")
    if len(cleaned) >= width:
        return cleaned[:width]
    return cleaned.rjust(width, "0")


def _attr(key: str, value: Any) -> Dict[str, Any]:
    """One OTLP attribute, typed by what the value actually is.

    Numbers stay numeric so Langfuse can aggregate cost and tokens without
    parsing strings back out; everything structured is JSON, which is what the
    Langfuse OTLP mapper expects for input/output.
    """
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    if isinstance(value, str):
        return {"key": key, "value": {"stringValue": value}}
    return {"key": key, "value": {"stringValue": json.dumps(value, default=str)}}


class LangfuseOTLPSink:
    """Emits watcher traces as OTLP spans carrying Langfuse semantics.

    Buffers for the same reason the classic sink does — a trace is sent as one
    request when it closes, so parents and children arrive together and the
    graph is never drawn from a half-delivered tree.
    """

    def __init__(self, endpoint: Optional[str] = None, timeout: float = 5.0):
        config = get_config()
        host = (endpoint or config.langfuse_host or "").rstrip("/")
        self._endpoint = f"{host}/api/public/otel/v1/traces" if host else ""
        self._timeout = timeout
        self._auth = ""
        if config.langfuse_public_key and config.langfuse_secret_key:
            raw = f"{config.langfuse_public_key}:{config.langfuse_secret_key}"
            self._auth = base64.b64encode(raw.encode()).decode()

        self._lock = threading.Lock()
        self._pending: Dict[str, List[Dict[str, Any]]] = {}
        self._roots: Dict[str, Dict[str, Any]] = {}

    @property
    def available(self) -> bool:
        return bool(self._endpoint and self._auth)

    # -- lifecycle ------------------------------------------------------

    def start_trace(self, trace_id: str, name: str, context: TraceContext,
                    input_data: Any = None) -> None:
        """Open the root span.

        OTLP has no trace-level record: a trace *is* its root span, so trace
        attributes (user, session, tags) hang off this one. It is typed
        ``agent`` because the root of a watcher trace is the agent turn —
        which is also what makes it the graph's entry node.
        """
        if not self.available:
            return
        config = get_config()
        with self._lock:
            self._roots[trace_id] = {
                "name": name,
                "input": input_data,
                "context": context,
                "span_id": _hex_id(secrets.token_hex(8), _SPAN_ID_HEX),
                "start_ns": None,
                "attributes": [
                    _attr("langfuse.observation.type", "agent"),
                    _attr("langfuse.trace.name", name),
                    _attr("service.name", config.service_name),
                    _attr("langfuse.environment", config.environment),
                ],
            }
            self._pending.setdefault(trace_id, [])

    def record(self, event: Event, context: TraceContext) -> None:
        if not self.available or not context.trace_id:
            return
        with self._lock:
            root = self._roots.get(context.trace_id)
            self._pending.setdefault(context.trace_id, []).append(
                self._build_span(event, context,
                                 root_span_id=root["span_id"] if root else None)
            )

    def end_trace(self, trace_id: str, output: Any = None,
                  metadata: Optional[Dict[str, Any]] = None) -> None:
        if not self.available:
            return
        with self._lock:
            root = self._roots.pop(trace_id, None)
            spans = self._pending.pop(trace_id, [])

        if root is not None:
            spans.insert(0, self._build_root(trace_id, root, output, metadata, spans))
        if not spans:
            return
        self._send(trace_id, spans)

    # -- span construction ----------------------------------------------

    def _build_root(self, trace_id: str, root: Dict[str, Any], output: Any,
                    metadata: Optional[Dict[str, Any]],
                    children: List[Dict[str, Any]]) -> Dict[str, Any]:
        context: TraceContext = root["context"]
        config = get_config()
        attributes = list(root["attributes"])

        if context.user_id:
            attributes.append(_attr("langfuse.user.id", str(context.user_id)))
        if context.session_id:
            attributes.append(_attr("langfuse.session.id", str(context.session_id)))

        tags = context.merged_tags([
            f"service:{config.service_name}",
            f"env:{config.environment}",
            *( [f"company:{context.company_id}"] if context.company_id else [] ),
            *( [f"cost_center:{context.cost_center}"] if context.cost_center else [] ),
        ])
        if tags:
            attributes.append(_attr("langfuse.trace.tags", tags))

        trace_metadata = context.merged_metadata({
            "release": config.release,
            "cost_center": context.cost_center,
            "company_id": context.company_id,
            "channel": context.channel,
            **(metadata or {}),
        })
        # One JSON attribute, not `langfuse.trace.metadata.<key>` per entry.
        # The dotted form is accepted by the HTTP endpoint — it answers 200 —
        # and then the asynchronous ingestion job drops the entire trace, so
        # the only symptom is a 404 on lookup some seconds later.
        trace_metadata = {k: v for k, v in trace_metadata.items() if v is not None}
        if trace_metadata:
            attributes.append(_attr("langfuse.trace.metadata", trace_metadata))

        if root.get("input") is not None:
            attributes.append(_attr("langfuse.trace.input", root["input"]))
        if output is not None:
            attributes.append(_attr("langfuse.trace.output", output))

        # The root must enclose its children in time or the graph's timeline
        # collapses; children were recorded after start_trace, so their extent
        # is the honest span of the turn.
        starts = [int(c["startTimeUnixNano"]) for c in children] or None
        ends = [int(c["endTimeUnixNano"]) for c in children] or None
        now_ns = _now_ns()
        start_ns = min(starts) if starts else now_ns
        end_ns = max(ends) if ends else now_ns

        return {
            "traceId": _hex_id(trace_id, _TRACE_ID_HEX),
            "spanId": root["span_id"],
            "name": root["name"],
            "kind": 1,
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(max(end_ns, start_ns)),
            "attributes": attributes,
        }

    def _build_span(self, event: Event, context: TraceContext,
                    root_span_id: Optional[str] = None) -> Dict[str, Any]:
        observation_type = OBSERVATION_TYPES.get(event.type, "span")
        attributes = [
            _attr("langfuse.observation.type", observation_type),
            _attr("langfuse.observation.level", event.severity.value),
        ]

        if event.input is not None:
            attributes.append(_attr("langfuse.observation.input", event.input))
        if event.output is not None:
            attributes.append(_attr("langfuse.observation.output", event.output))
        if event.status_message:
            attributes.append(
                _attr("langfuse.observation.status_message", event.status_message)
            )

        if isinstance(event, GenerationEvent):
            attributes.extend(self._generation_attributes(event))

        payload = {k: v for k, v in event.to_payload().items() if v is not None}
        if payload:
            # Single JSON attribute — see the note in _build_root on why the
            # per-key dotted form silently loses the trace.
            attributes.append(_attr("langfuse.observation.metadata", payload))

        start_ns = int(event.started_at * 1e9)
        end_ns = int((event.ended_at or event.started_at) * 1e9)

        span: Dict[str, Any] = {
            "traceId": _hex_id(context.trace_id, _TRACE_ID_HEX),
            "spanId": _hex_id(event.id, _SPAN_ID_HEX),
            "name": event.name,
            "kind": 1,
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(max(end_ns, start_ns)),
            "attributes": attributes,
            "status": {"code": _STATUS_CODES.get(event.severity, 0)},
        }
        # A top-level event has no parent *event* — its parent is the trace,
        # which in OTLP is the root span rather than a separate record. Left
        # unset it becomes a second root under the same trace id, and the
        # ingestion job discards the whole trace: HTTP 200 on export, then a
        # 404 on lookup, with nothing in between to explain it.
        parent = event.parent_id or root_span_id
        if parent:
            span["parentSpanId"] = _hex_id(parent, _SPAN_ID_HEX)
        return span

    @staticmethod
    def _generation_attributes(event: GenerationEvent) -> List[Dict[str, Any]]:
        """Model, usage and cost under the keys Langfuse aggregates on.

        ``gen_ai.*`` is the OpenTelemetry GenAI convention; the ``langfuse.*``
        cost keys have no OTel equivalent and are what the cost dashboards
        read.
        """
        attributes = [
            _attr("gen_ai.request.model", event.model),
            _attr("gen_ai.usage.input_tokens", event.prompt_tokens),
            _attr("gen_ai.usage.output_tokens", event.completion_tokens),
            _attr("langfuse.observation.usage_details",
                  {"input": event.prompt_tokens,
                   "output": event.completion_tokens,
                   "total": event.total_tokens}),
        ]
        if event.provider:
            attributes.append(_attr("gen_ai.system", event.provider))
        if event.total_cost:
            attributes.append(
                _attr("langfuse.observation.cost_details", {"total": event.total_cost})
            )
        # Links the observation to a registry version, so "which prompt
        # produced this" is answerable from the graph itself.
        name = event.prompt.get("prompt_name")
        version = event.prompt.get("prompt_version")
        if name:
            # One JSON attribute. Split into `…prompt.name` / `…prompt.version`
            # the ingestion job discards the generation while keeping the rest
            # of the trace — a graph with the LLM call missing from it.
            link = {"name": str(name)}
            if version:
                link["version"] = str(version)
            attributes.append(_attr("langfuse.observation.prompt", link))
        return attributes

    # -- transport ------------------------------------------------------

    def _send(self, trace_id: str, spans: List[Dict[str, Any]]) -> None:
        config = get_config()
        payload = {
            "resourceSpans": [{
                "resource": {"attributes": [
                    _attr("service.name", config.service_name),
                    _attr("service.version", config.release or "unknown"),
                    _attr("deployment.environment.name", config.environment),
                ]},
                "scopeSpans": [{
                    "scope": {"name": "shipit_watcher"},
                    "spans": spans,
                }],
            }]
        }
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload, default=str).encode(),
            headers={
                "Authorization": f"Basic {self._auth}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                if response.status >= 300:
                    logger.warning("watcher: otlp export returned %s", response.status)
        except urllib.error.HTTPError as exc:
            logger.warning("watcher: otlp export failed %s: %s",
                           exc.code, exc.read()[:300])
        except Exception:
            # Observability must never take the request down with it.
            logger.warning("watcher: otlp export failed", exc_info=True)

    def record_score(self, score: Any) -> None:
        """Scores have no OTLP representation — they go over the REST API.

        Deliberately a separate client call rather than a span attribute: a
        score can be attached long after the trace closed (a thumbs-up an hour
        later), so it cannot ride along with the export.
        """
        config = get_config()
        if not config.has_langfuse_credentials:
            return
        try:
            from langfuse import Langfuse

            client = Langfuse(
                public_key=config.langfuse_public_key,
                secret_key=config.langfuse_secret_key,
                host=config.langfuse_host,
            )
            payload = {
                "name": score.name,
                "value": score.numeric_value
                if score.numeric_value is not None else score.value,
                "comment": score.comment or None,
                "trace_id": _hex_id(score.trace_id, _TRACE_ID_HEX),
            }
            if score.observation_id:
                payload["observation_id"] = _hex_id(score.observation_id, _SPAN_ID_HEX)
            client.score(**{k: v for k, v in payload.items() if v is not None})
            client.flush()
        except Exception:
            logger.warning("watcher: otlp sink score failed", exc_info=True)

    def flush(self) -> None:
        """Export anything still buffered.

        Reachable when a process exits mid-trace; a partial graph is more
        useful than none, and the alternative is silently losing the turn
        that was in flight.
        """
        with self._lock:
            outstanding = list(self._pending.items())
            self._pending.clear()
            self._roots.clear()
        for trace_id, spans in outstanding:
            if spans:
                self._send(trace_id, spans)


def _now_ns() -> int:
    import time

    return int(time.time() * 1e9)
