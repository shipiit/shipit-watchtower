"""
Shipit Watcher — observability for LLM applications.

A single, coherent record of what an AI system did: which prompt ran, what it
cost, which tenant it belonged to, which tools it called, what it retrieved and
why it chose what it chose.

Quick start
-----------

::

    import shipit_watcher as wt

    wt.configure(service_name="my-app", environment="production")
    wt.instrument_litellm()          # one trace per call, not three

    with wt.trace("chat.request", company_id=str(company.id),
                  cost_center="support-ops", user_id=str(user.id)):
        with wt.get_tracer().tool("search_docs") as tool:
            tool.output = search(query)

        wt.get_tracer().decision(
            "route", chosen="billing-expert",
            options=["billing-expert", "support-expert"],
            rationale="query mentions billing",
        )

Design rules
------------

* Observability never breaks the request it observes. Every entry point
  swallows its own failures.
* Exactly one component owns tracing. See
  :mod:`shipit_watcher.instrumentation.litellm`.
* PII is masked *before* anything is persisted or leaves the process.
* Context propagates through :mod:`contextvars`, so nothing needs a
  ``trace_id`` parameter threaded through it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from .config import WatcherConfig, configure, get_config, reset_config
from .context import TraceContext, bind, current_context, get_trace_id, use_prompt
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
from .llm import run_prompt as run_prompt
from .llm import GovernanceError, LLMClient, LLMResponse, complete, stream
from .masking import MaskingPolicy, Redactor, mask_payload, mask_text
from .prompts import (
    ManagedPrompt, PromptRegistry, agent_prompt_name, create_prompt,
    get_agent_prompt, get_prompt, get_registry,
)
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
from .tracer import (
    decision as decision, generation as generation, handoff as handoff,
    policy as policy, retrieval as retrieval, span as span, tool as tool,
)
from .tracer import Tracer, get_tracer

__version__ = "1.1.0"


# NOTE: there is deliberately no module-level ``tracer()`` helper. Defining one
# would shadow the ``shipit_watcher.tracer`` submodule, so
# ``import shipit_watcher.tracer`` would bind a function instead of the module.
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
    "WatcherConfig", "configure", "get_config", "reset_config",
    # context
    "TraceContext", "bind", "use_prompt", "current_context", "get_trace_id",
    # tracing
    "Tracer", "get_tracer", "trace", "flush",
    "span", "tool", "generation", "retrieval", "decision", "handoff", "policy",
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
    # LLM gateway
    "LLMClient", "LLMResponse", "GovernanceError", "complete", "stream", "run_prompt",
    # prompt registry
    "ManagedPrompt", "PromptRegistry", "get_prompt", "create_prompt",
    "get_agent_prompt", "agent_prompt_name", "get_registry",
    # scoring & evaluation
    "Score", "ScoreSource", "ScoreDataType", "score", "record_score",
    "Evaluator", "LLMJudge", "JUDGE_RUBRICS", "evaluate",
    # instrumentation
    "instrument_litellm",
]
