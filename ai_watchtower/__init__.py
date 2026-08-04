"""
AI Watchtower — observability for LLM applications.

A single, coherent record of what an AI system did: which prompt ran, what it
cost, which tenant it belonged to, which tools it called, what it retrieved and
why it chose what it chose.

Quick start
-----------

::

    import ai_watchtower as wt

    wt.configure(service_name="fleetflow", environment="production")
    wt.instrument_litellm()          # one trace per call, not three

    with wt.trace("chat.request", company_id=str(company.id),
                  cost_center="fleet-ops", user_id=str(user.id)):
        with wt.get_tracer().tool("search_fleet") as tool:
            tool.output = search(query)

        wt.get_tracer().decision(
            "route", chosen="fuel-expert",
            options=["fuel-expert", "driver-expert"],
            rationale="query mentions consumption",
        )

Design rules
------------

* Observability never breaks the request it observes. Every entry point
  swallows its own failures.
* Exactly one component owns tracing. See
  :mod:`ai_watchtower.instrumentation.litellm`.
* PII is masked *before* anything is persisted or leaves the process.
* Context propagates through :mod:`contextvars`, so nothing needs a
  ``trace_id`` parameter threaded through it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from .config import WatchtowerConfig, configure, get_config, reset_config
from .context import TraceContext, bind, current_context, get_trace_id
from .events import (
    DecisionEvent,
    Event,
    EventType,
    GenerationEvent,
    HandoffEvent,
    HumanReviewEvent,
    PolicyEvent,
    RetrievalEvent,
    RetrievedChunk,
    Severity,
    ToolInvocationEvent,
)
from .decorators import observe, observe_agent, observe_tool
from .identity import PromptIdentity, fingerprint_text, identify_prompt
from .masking import MaskingPolicy, Redactor, mask_payload, mask_text
from .prompts import ManagedPrompt, PromptRegistry, get_prompt, get_registry
from .scoring import (
    Evaluator,
    JUDGE_RUBRICS,
    LLMJudge,
    Score,
    ScoreDataType,
    ScoreSource,
    evaluate,
    record_score,
    score,
)
from .tracer import Tracer, get_tracer

__version__ = "1.0.0"


# NOTE: there is deliberately no module-level ``tracer()`` helper. Defining one
# would shadow the ``ai_watchtower.tracer`` submodule, so
# ``import ai_watchtower.tracer`` would bind a function instead of the module.
# ``get_tracer()`` is the single, unambiguous accessor.


@contextmanager
def trace(name: str, *, input: Any = None, **context_fields) -> Iterator[TraceContext]:
    """Open a root trace. Shorthand for ``get_tracer().trace(...)``."""
    with get_tracer().trace(name, input=input, **context_fields) as context:
        yield context


def instrument_litellm(*, replace_langfuse_callback: bool = True) -> bool:
    """Take ownership of LiteLLM call tracing.

    Removes LiteLLM's built-in Langfuse callback so a call is traced once, by
    this SDK, inside the ambient trace — rather than twice, flat and
    unparented, with no tenant or prompt metadata.
    """
    from .instrumentation.litellm import instrument

    return instrument(replace_langfuse_callback=replace_langfuse_callback)


def flush() -> None:
    """Force delivery of buffered events. Call before a process exits."""
    get_tracer().flush()


__all__ = [
    "__version__",
    # configuration
    "WatchtowerConfig", "configure", "get_config", "reset_config",
    # context
    "TraceContext", "bind", "current_context", "get_trace_id",
    # tracing
    "Tracer", "get_tracer", "trace", "flush",
    # decorators
    "observe", "observe_tool", "observe_agent",
    # events
    "Event", "EventType", "Severity", "GenerationEvent", "DecisionEvent",
    "ToolInvocationEvent", "RetrievalEvent", "RetrievedChunk", "HandoffEvent",
    "PolicyEvent", "HumanReviewEvent",
    # prompt identity
    "PromptIdentity", "identify_prompt", "fingerprint_text",
    # privacy
    "MaskingPolicy", "Redactor", "mask_text", "mask_payload",
    # prompt registry
    "ManagedPrompt", "PromptRegistry", "get_prompt", "get_registry",
    # scoring & evaluation
    "Score", "ScoreSource", "ScoreDataType", "score", "record_score",
    "Evaluator", "LLMJudge", "JUDGE_RUBRICS", "evaluate",
    # instrumentation
    "instrument_litellm",
]
