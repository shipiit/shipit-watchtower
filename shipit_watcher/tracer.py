"""
The tracer — the one object application code talks to.

Design commitments:

* **Context managers, not manual pairing.** Every span is opened and closed by
  ``with``. Instrumentation that relies on remembering to call ``end()``
  degrades the moment an exception takes an early return, and a half-closed
  span is worse than none — it reports a duration of zero and hides the error.
* **Exceptions are recorded, then re-raised.** The tracer never changes program
  behaviour. It marks the span ERROR, attaches the exception, and lets it
  propagate untouched.
* **Sampling never drops errors.** A 10% sample that also throws away 90% of
  failures is useless precisely when it is needed. Error traces are always kept.
* **Nothing raises out of the tracer.** If observability breaks, the request
  still succeeds.
* **A cancelled or abandoned request is still a trace.** The handlers around
  every ``yield`` catch ``BaseException``, not ``Exception``, because
  ``asyncio.CancelledError`` and ``GeneratorExit`` are neither — and a client
  disconnect, a request timeout, and an abandoned SSE stream all arrive as
  one of those. Caught only as ``Exception``, they skipped both the error and
  the success path: the trace was opened, never closed, never exported, and
  its buffers were retained for the life of the process. The requests worth
  investigating are exactly the ones that were disappearing. The exception is
  recorded and re-raised untouched, as always.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from .config import WatcherConfig, get_config
from .context import TraceContext, bind, current_context
from .events import (
    DecisionEvent,
    Event,
    GenerationEvent,
    HandoffEvent,
    PolicyEvent,
    RetrievalEvent,
    RetrievedChunk,
    Severity,
    ToolInvocationEvent,
)
from .identity import PromptIdentity
from .masking import mask_payload
from .sinks.base import ConsoleSink, FanOutSink, Sink

logger = logging.getLogger(__name__)

__all__ = ["Tracer", "get_tracer"]


class Tracer:
    """Creates traces and records typed events against configured sinks."""

    def __init__(self, sinks: list[Sink] | None = None,
                 config: WatcherConfig | None = None):
        self._config = config
        self._explicit_sinks = sinks
        self._sink: FanOutSink | None = None
        self._tail_buffers: dict[str, list[Event]] = {}
        self._tail_lock = threading.Lock()

    # -- wiring ---------------------------------------------------------

    @property
    def config(self) -> WatcherConfig:
        return self._config or get_config()

    @property
    def sink(self) -> FanOutSink:
        """Sinks, built lazily so import order never forces configuration."""
        if self._sink is None:
            self._sink = FanOutSink(self._explicit_sinks or self._default_sinks())
        return self._sink

    def _default_sinks(self) -> list[Sink]:
        config = self.config
        sinks: list[Sink] = []

        # Explicit bundles win over environment discovery and may come from
        # third-party packages. A bare Sink is accepted for convenience; a
        # capability bundle exposes its trace backend under `.trace`.
        for backend in config.backends:
            candidate = getattr(backend, "trace", backend)
            if candidate is not None:
                sinks.append(candidate)

        if config.has_langfuse_credentials:
            try:
                backend_sink: Sink
                if (config.langfuse_transport or "otlp").lower() == "otlp":
                    from .sinks.langfuse_otel_sink import LangfuseOTLPSink

                    backend_sink = LangfuseOTLPSink()
                else:
                    from .sinks.langfuse_sink import LangfuseSink

                    backend_sink = LangfuseSink()
                if getattr(backend_sink, "available", True):
                    sinks.append(backend_sink)
            except Exception:
                logger.warning("watcher: Langfuse sink unavailable", exc_info=True)

        if config.persist_to_database:
            try:
                from .sinks.django_sink import DjangoSink

                sinks.append(DjangoSink())
            except Exception:
                logger.warning("watcher: Django sink unavailable", exc_info=True)

        if config.has_phoenix_config and not any(
            type(s).__name__ == "PhoenixOTLPSink" for s in sinks
        ):
            try:
                from .sinks.phoenix_sink import PhoenixOTLPSink

                phoenix_sink: Any = PhoenixOTLPSink()
                if phoenix_sink.available:
                    sinks.append(phoenix_sink)
            except Exception:
                logger.warning("watcher: Phoenix sink unavailable", exc_info=True)

        if config.has_langsmith_credentials and not any(
            type(s).__name__ == "LangSmithOTLPSink" for s in sinks
        ):
            try:
                from .sinks.langsmith_sink import LangSmithOTLPSink

                langsmith_sink: Any = LangSmithOTLPSink()
                if langsmith_sink.available:
                    sinks.append(langsmith_sink)
            except Exception:
                logger.warning("watcher: LangSmith sink unavailable", exc_info=True)

        if config.dashboard_url and not any(
            type(s).__name__ == "DashboardSink" for s in sinks
        ):
            try:
                from .sinks.dashboard_sink import DashboardSink

                dashboard_sink: Any = DashboardSink()
                if dashboard_sink.available:
                    sinks.append(dashboard_sink)
            except Exception:
                logger.warning("watcher: dashboard sink unavailable", exc_info=True)

        if not sinks and config.environment == "development":
            sinks.append(ConsoleSink())

        return sinks

    # -- activation -----------------------------------------------------

    @property
    def active(self) -> bool:
        """Whether this tracer will emit anything.

        Deliberately a property of the *tracer*, not of the config: a tracer
        constructed with explicit sinks (tests, a custom backend) is active
        even when no Langfuse credentials are configured. Gating on config
        alone silently disabled every hand-wired tracer.
        """
        if not self.config.enabled:
            return False
        return bool(self.sink)

    # -- sampling -------------------------------------------------------

    def _should_sample(self) -> bool:
        rate = self.config.sample_rate
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        return random.random() < rate

    def _prepare(self, value: Any, *, content: bool = True) -> Any:
        """Apply the privacy policy to anything leaving the process."""
        config = self.config
        policy = config.effective_content_policy
        if policy == "none" or (policy == "metadata" and content):
            return None
        if policy in {"metadata", "redacted"}:
            value = mask_payload(value)
        if isinstance(value, str) and len(value) > config.max_content_chars:
            return value[: config.max_content_chars] + "…[truncated]"
        return value

    def _prepare_context(self, context: TraceContext) -> TraceContext:
        """Sanitise caller-controlled context fields before a backend sees them."""
        return replace(
            context,
            metadata=self._prepare(context.metadata, content=False) or {},
            tags=self._prepare(context.tags, content=False) or [],
        )

    def _prepare_event(self, event: Event) -> Event:
        """Apply the privacy boundary to every content-bearing event field.

        Input/output were historically the only fields prepared centrally.
        Typed payloads also contain free text (decision rationales, retrieved
        snippets, policy reasons and tool errors), so sinks could otherwise
        receive raw PII despite the process-wide masking guarantee.
        """
        event.input = self._prepare(event.input)
        event.output = self._prepare(event.output)
        event.status_message = self._prepare(event.status_message) or ""
        event.metadata = self._prepare(event.metadata, content=False) or {}
        event.tags = self._prepare(event.tags, content=False) or []

        if isinstance(event, DecisionEvent):
            event.chosen = self._prepare(event.chosen) or ""
            event.options_considered = self._prepare(event.options_considered) or []
            event.rationale = self._prepare(event.rationale) or ""
        elif isinstance(event, ToolInvocationEvent):
            event.arguments = self._prepare(event.arguments)
            event.error = self._prepare(event.error) or ""
        elif isinstance(event, RetrievalEvent):
            event.query = self._prepare(event.query) or ""
            event.knowledge_base = self._prepare(event.knowledge_base) or ""
            for chunk in event.chunks:
                chunk.source = self._prepare(chunk.source) or ""
                chunk.version = self._prepare(chunk.version)
                chunk.snippet = self._prepare(chunk.snippet) or ""
        elif isinstance(event, HandoffEvent):
            event.from_agent = self._prepare(event.from_agent) or ""
            event.to_agent = self._prepare(event.to_agent) or ""
            event.reason = self._prepare(event.reason) or ""
        elif isinstance(event, PolicyEvent):
            event.policy_name = self._prepare(event.policy_name) or ""
            event.reason = self._prepare(event.reason) or ""
        return event

    # -- traces ---------------------------------------------------------

    @contextmanager
    def trace(self, name: str, *, input: Any = None,
              **context_fields) -> Iterator[TraceContext]:
        """Open a root trace and bind it as the ambient context.

            with tracer.trace("chat.request", company_id=cid, cost_center="support-ops"):
                ...

        Everything emitted inside the block attaches to this trace without
        being passed a handle.
        """
        remote_trace_id = context_fields.pop("trace_id", None)
        remote_parent_id = context_fields.pop(
            "remote_parent_id", context_fields.pop("parent_id", None)
        )
        upstream_sampled = context_fields.pop("sampled", True)

        if not self.active:
            with bind(**context_fields) as context:
                yield context
            return


        if not upstream_sampled or not self._should_sample():
            # Keep an ambient id even when sampled out. Otherwise LiteLLM's
            # callback sees "no trace" and creates a standalone generation,
            # accidentally defeating sampling. If the body fails, promote a
            # minimal error trace so sampling never hides failures.
            trace_id = remote_trace_id or uuid.uuid4().hex
            root_span_id = uuid.uuid4().hex[:16]
            self._open_tail_buffer(trace_id)
            with bind(trace_id=trace_id, root_span_id=root_span_id, parent_id=None,
                      remote_parent_id=remote_parent_id,
                      result={}, sampled=False,
                      **context_fields) as context:
                started = time.time()
                try:
                    yield context
                except BaseException as exc:
                    self._promote_tail_trace(
                        trace_id, name, context, input,
                        {"error": f"{type(exc).__name__}: {exc}"},
                        duration_ms=int((time.time() - started) * 1000),
                    )
                    raise
                else:
                    duration_ms = int((time.time() - started) * 1000)
                    with self._tail_lock:
                        buffered = list(self._tail_buffers.get(trace_id, ()))
                    costly = sum(
                        event.total_cost for event in buffered
                        if isinstance(event, GenerationEvent)
                    )
                    blocked = any(
                        isinstance(event, PolicyEvent) and event.blocked
                        for event in buffered
                    )
                    config = self.config
                    slow = config.slow_trace_ms > 0 and duration_ms >= config.slow_trace_ms
                    expensive = (
                        config.expensive_trace_usd > 0
                        and costly >= config.expensive_trace_usd
                    )
                    if blocked or slow or expensive:
                        self._promote_tail_trace(
                            trace_id, name, context, input,
                            context.result.get("output"), duration_ms=duration_ms,
                        )
                    else:
                        with self._tail_lock:
                            self._tail_buffers.pop(trace_id, None)
            return

        trace_id = remote_trace_id or uuid.uuid4().hex
        root_span_id = uuid.uuid4().hex[:16]

        # NOTE: no broad try/except around the yield. A generator-based context
        # manager may yield exactly once, so catching the body's exception here
        # and yielding again in the handler raises "generator didn't stop after
        # throw()" — masking the user's real error with a confusing one. Sink
        # calls are guarded individually instead; the body's exception is
        # recorded and re-raised untouched.
        # `result={}` is not redundant. `bind` builds the new context with
        # dataclasses.replace, which copies the *reference* to the parent's
        # result dict — so without this every trace shares one dict and a new
        # trace reports the previous trace's output as its own. In production
        # that showed up as background traces all carrying the last chat
        # answer, which is worse than a missing output: it is a confident
        # wrong one.
        # `sampled=True` is not redundant either. This trace was selected for
        # export, but `bind` copies the *parent's* fields — so a sampled-in
        # trace opened inside a sampled-out one inherited `sampled=False`,
        # exported a root span with zero children, and left its tail buffer
        # behind forever. Any deployment with sample_rate < 1.0 and nested
        # traces (a LangGraph run inside a request, an experiment inside a
        # job) hit it.
        with bind(trace_id=trace_id, root_span_id=root_span_id, parent_id=None,
                  remote_parent_id=remote_parent_id,
                  result={}, sampled=True,
                  **context_fields) as context:
            try:
                self.sink.start_trace(
                    trace_id, name, self._prepare_context(context), self._prepare(input)
                )
            except Exception:
                logger.warning("watcher: start_trace failed", exc_info=True)

            started = time.time()
            try:
                yield context
            except BaseException as exc:
                self._safe_end_trace(
                    trace_id,
                    {"error": f"{type(exc).__name__}: {exc}"},
                    duration_ms=int((time.time() - started) * 1000),
                )
                raise
            else:
                # Whatever the caller recorded via ctx.set_output(); passing
                # None here is what rendered as "undefined" in the UI.
                self._safe_end_trace(
                    trace_id,
                    context.result.get("output"),
                    duration_ms=int((time.time() - started) * 1000),
                )

    def _open_tail_buffer(self, trace_id: str) -> None:
        """Start buffering a sampled-out trace, evicting the oldest if full."""
        limit = max(1, self.config.tail_buffer_max_traces)
        with self._tail_lock:
            while len(self._tail_buffers) >= limit:
                oldest = next(iter(self._tail_buffers))
                self._tail_buffers.pop(oldest, None)
                logger.debug(
                    "watcher: tail buffer limit reached; dropped trace %s", oldest
                )
            self._tail_buffers[trace_id] = []

    def _safe_end_trace(self, trace_id: str, output: Any,
                        duration_ms: int = 0) -> None:
        try:
            self.sink.end_trace(
                trace_id, self._prepare(output), {"duration_ms": duration_ms}
            )
        except Exception:
            logger.warning("watcher: end_trace failed", exc_info=True)

    def _promote_tail_trace(
        self,
        trace_id: str,
        name: str,
        context: TraceContext,
        input_data: Any,
        output: Any,
        *,
        duration_ms: int,
    ) -> None:
        """Export a previously sampled-out trace when its outcome merits it."""
        promoted = self._prepare_context(replace(context, sampled=True))
        with self._tail_lock:
            buffered = self._tail_buffers.pop(trace_id, [])
        try:
            self.sink.start_trace(trace_id, name, promoted, self._prepare(input_data))
            for event in buffered:
                self.sink.record(event, promoted)
        except Exception:
            logger.warning("watcher: tail trace promotion failed", exc_info=True)
        self._safe_end_trace(trace_id, output, duration_ms=duration_ms)

    # -- spans ----------------------------------------------------------

    @contextmanager
    def span(self, name: str, *, event: Event | None = None,
             input: Any = None, **metadata) -> Iterator[Event]:
        """Open a child span. Yields the event so the caller can enrich it.

        The yielded object is the live event — set ``output``, or mutate a
        typed subclass's fields, and the change is recorded on exit.
        """
        context = current_context()
        node = event or Event(name=name)
        node.name = name
        node.parent_id = context.parent_id
        node.input = self._prepare(input)
        node.metadata.update(metadata)

        if not self.active or context.trace_id is None:
            yield node
            return

        started = time.time()
        try:
            # Children of this span nest beneath it.
            with bind(parent_id=node.id, depth=context.depth + 1):
                yield node
        except BaseException as exc:
            node.finish(severity=Severity.ERROR, status_message=f"{type(exc).__name__}: {exc}")
            self._emit(node, context)
            raise
        else:
            if node.ended_at is None:
                node.ended_at = time.time()
            node.output = self._prepare(node.output)
            self._emit(node, context)
        finally:
            if node.ended_at is None:
                node.ended_at = started

    def _emit(self, event: Event, context: TraceContext) -> None:
        """Record one event, subject to the same guard as spans.

        decision()/retrieval()/handoff()/policy() call this directly, so the
        check lives here rather than in each of them — otherwise a disabled
        tracer still emitted typed events while dropping spans.
        """
        if not self.active or context.trace_id is None:
            return
        prepared = self._prepare_event(event)
        if not context.sampled:
            with self._tail_lock:
                # `get`, not `setdefault`. LiteLLM's streaming callback fires
                # on another thread *after* the trace closed, and setdefault
                # re-created a buffer nothing would ever pop — one permanent
                # entry per sampled-out streaming request. A late event for a
                # finished trace has nowhere to go, and dropping it is the
                # honest outcome.
                buffered = self._tail_buffers.get(context.trace_id)
                if buffered is not None and len(buffered) < self.config.tail_buffer_max_events:
                    buffered.append(prepared)
            return
        try:
            self.sink.record(
                prepared, self._prepare_context(context)
            )
        except Exception:
            logger.warning("watcher: record failed", exc_info=True)

    def record_event(
        self, event: Event, context: TraceContext | None = None
    ) -> None:
        """Record an event completed by a callback-based integration.

        Callback frameworks open and close work in separate hooks, so they
        cannot safely use a Python context manager. This public bridge keeps
        those adapters on the same privacy, sampling, and delivery path.
        """
        self._emit(event, context or current_context())

    # -- typed helpers --------------------------------------------------

    @contextmanager
    def generation(self, name: str, *, model: str = "", provider: str = "",
                   prompt: PromptIdentity | None = None,
                   input: Any = None, **metadata) -> Iterator[GenerationEvent]:
        """An LLM call. Set usage/cost on the yielded event as they become known."""
        node = GenerationEvent(name=name, model=model, provider=provider)
        if prompt is not None:
            node.prompt = prompt.as_metadata()
            node.tags.extend(prompt.as_tags())
        else:
            # Fall back to whatever `wt.use_prompt(...)` bound, so a call site
            # that cannot reach the identity is still attributed.
            ambient = current_context().prompt
            if ambient:
                node.prompt = dict(ambient)
        with self.span(name, event=node, input=input, **metadata):
            yield node

    @contextmanager
    def tool(self, tool_name: str, *, arguments: Any = None,
             **metadata) -> Iterator[ToolInvocationEvent]:
        """A tool invocation. Marks itself failed if the block raises."""
        node = ToolInvocationEvent(name=f"tool.{tool_name}", tool_name=tool_name,
                                   arguments=self._prepare(arguments))
        with self.span(node.name, event=node, input=arguments, **metadata):
            try:
                yield node
            except BaseException as exc:
                # Set type-specific failure fields before span() serialises the
                # event. Immediate sinks must not receive succeeded=True on an
                # ERROR tool observation.
                node.succeeded = False
                node.error = str(exc)
                raise

    def decision(self, name: str, *, chosen: str, options: list[str],
                 rationale: str = "", confidence: float | None = None) -> None:
        """Record a branch point and the alternatives that were rejected."""
        node = DecisionEvent(
            name=name, chosen=chosen, options_considered=list(options),
            rationale=rationale, confidence=confidence,
        )
        node.parent_id = current_context().parent_id
        node.finish()
        self._emit(node, current_context())

    def retrieval(self, name: str, *, query: str, chunks: list[RetrievedChunk],
                  knowledge_base: str = "") -> None:
        """Record a RAG lookup together with per-chunk provenance."""
        node = RetrievalEvent(
            name=name, query=self._prepare(query),
            chunks=chunks, knowledge_base=knowledge_base,
        )
        node.parent_id = current_context().parent_id
        node.finish()
        self._emit(node, current_context())

    def handoff(self, *, from_agent: str, to_agent: str, reason: str = "") -> None:
        """Record delegation from one agent to another."""
        node = HandoffEvent(
            name=f"handoff.{from_agent}->{to_agent}",
            from_agent=from_agent, to_agent=to_agent, reason=reason,
        )
        node.parent_id = current_context().parent_id
        node.finish()
        self._emit(node, current_context())

    def policy(
        self,
        policy_name: str,
        *,
        blocked: bool = False,
        reason: str = "",
        **metadata: Any,
    ) -> None:
        """Record a guardrail decision — the audit trail for enforce mode."""
        node = PolicyEvent(
            name=f"policy.{policy_name}", policy_name=policy_name,
            blocked=blocked, reason=reason,
            severity=Severity.WARNING if blocked else Severity.DEFAULT,
            metadata=metadata,
        )
        node.parent_id = current_context().parent_id
        node.finish()
        self._emit(node, current_context())

    def flush(self) -> None:
        """Force delivery. Call before a worker process exits."""
        try:
            self.sink.flush()
        except Exception:
            logger.warning("watcher: flush failed", exc_info=True)


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """The process-wide tracer."""
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer


# ── Top-level shorthands ─────────────────────────────────────────────────
#
# `wt.tool(...)` rather than `wt.get_tracer().tool(...)`. These wrap the
# process tracer rather than duplicating it, so a test that swaps the tracer
# still intercepts calls made through them.

def span(name: str, **kwargs: Any):
    """Time an arbitrary step: ``with wt.span("parse.invoice"): ...``"""
    return get_tracer().span(name, **kwargs)


def tool(tool_name: str, **kwargs: Any):
    """Record a tool call — a ``tool`` node in the Langfuse agent graph."""
    return get_tracer().tool(tool_name, **kwargs)


def generation(name: str, **kwargs: Any):
    """Record an LLM call made outside :class:`~shipit_watcher.llm.LLMClient`."""
    return get_tracer().generation(name, **kwargs)


def retrieval(name: str, **kwargs: Any) -> None:
    """Record a RAG lookup with its provenance — a ``retriever`` graph node."""
    return get_tracer().retrieval(name, **kwargs)


def decision(name: str, **kwargs: Any) -> None:
    """Record a branch point, including the options not taken."""
    return get_tracer().decision(name, **kwargs)


def handoff(**kwargs: Any) -> None:
    """Record delegation to another agent — an ``agent`` graph node."""
    return get_tracer().handoff(**kwargs)


def policy(policy_name: str, **kwargs: Any) -> None:
    """Record a guardrail firing — a ``guardrail`` graph node."""
    return get_tracer().policy(policy_name, **kwargs)
