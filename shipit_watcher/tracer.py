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
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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

        if config.has_langfuse_credentials:
            try:
                if (config.langfuse_transport or "sdk").lower() == "otlp":
                    from .sinks.langfuse_otel_sink import LangfuseOTLPSink

                    sink = LangfuseOTLPSink()
                else:
                    from .sinks.langfuse_sink import LangfuseSink

                    sink = LangfuseSink()
                if sink.available:
                    sinks.append(sink)
            except Exception:
                logger.warning("watcher: Langfuse sink unavailable", exc_info=True)

        if config.persist_to_database:
            try:
                from .sinks.django_sink import DjangoSink

                sinks.append(DjangoSink())
            except Exception:
                logger.warning("watcher: Django sink unavailable", exc_info=True)

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

    def _prepare(self, value: Any) -> Any:
        """Apply the privacy policy to anything leaving the process."""
        config = self.config
        if not config.capture_content:
            return None
        if config.mask_pii:
            value = mask_payload(value)
        if isinstance(value, str) and len(value) > config.max_content_chars:
            return value[: config.max_content_chars] + "…[truncated]"
        return value

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
        if not self.active or not self._should_sample():
            with bind(**context_fields) as context:
                yield context
            return

        trace_id = uuid.uuid4().hex

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
        with bind(trace_id=trace_id, parent_id=None, result={},
                  **context_fields) as context:
            try:
                self.sink.start_trace(trace_id, name, context, self._prepare(input))
            except Exception:
                logger.warning("watcher: start_trace failed", exc_info=True)

            started = time.time()
            try:
                yield context
            except Exception as exc:
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

    def _safe_end_trace(self, trace_id: str, output: Any,
                        duration_ms: int = 0) -> None:
        try:
            self.sink.end_trace(
                trace_id, self._prepare(output), {"duration_ms": duration_ms}
            )
        except Exception:
            logger.warning("watcher: end_trace failed", exc_info=True)

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
        except Exception as exc:
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
        try:
            self.sink.record(event, context)
        except Exception:
            logger.warning("watcher: record failed", exc_info=True)

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
        try:
            with self.span(node.name, event=node, input=arguments, **metadata):
                yield node
        except Exception as exc:
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

    def policy(self, policy_name: str, *, blocked: bool = False, reason: str = "") -> None:
        """Record a guardrail decision — the audit trail for enforce mode."""
        node = PolicyEvent(
            name=f"policy.{policy_name}", policy_name=policy_name,
            blocked=blocked, reason=reason,
            severity=Severity.WARNING if blocked else Severity.DEFAULT,
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
