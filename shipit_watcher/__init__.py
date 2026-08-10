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

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .config import WatcherConfig, configure, get_config, reset_config
from .context import TraceContext, bind, current_context, get_trace_id, use_prompt
from .datasets import (
    DatasetItem,
    ExperimentResult,
    add_item,
    agent_dataset_name,
    capture,
    create_dataset,
    get_items,
    run_experiment,
)
from .decorators import observe, observe_agent, observe_tool
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
from .identity import PromptIdentity, fingerprint_text, identify_prompt
from .llm import GovernanceError, LLMClient, LLMResponse, complete, stream
from .llm import run_prompt as run_prompt
from .masking import MaskingPolicy, Redactor, mask_payload, mask_text
from .prompts import (
    ManagedPrompt,
    PromptRegistry,
    agent_prompt_name,
    create_prompt,
    get_agent_prompt,
    get_prompt,
    get_registry,
)
from .scoring import (
    JUDGE_RUBRICS,
    Evaluator,
    LLMJudge,
    Score,
    ScoreDataType,
    ScoreSource,
    evaluate,
    record_score,
    score,
)
from .tracer import Tracer, get_tracer
from .tracer import (
    decision as decision,
)
from .tracer import (
    generation as generation,
)
from .tracer import (
    handoff as handoff,
)
from .tracer import (
    policy as policy,
)
from .tracer import (
    retrieval as retrieval,
)
from .tracer import (
    span as span,
)
from .tracer import (
    tool as tool,
)

# Read from the installed distribution rather than typed here: this said
# 1.4.2 through two releases, so anything reporting the SDK version — a
# trace attribute, a bug report — was wrong and confidently so.
try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("shipit-watcher")
except Exception:  # not installed (a source checkout on sys.path)
    __version__ = "0.0.0+unknown"


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
    "JUDGE_RUBRICS",
    # datasets & experiments
    "DatasetItem",
    "DecisionEvent",
    "Evaluator",
    # events
    "Event",
    "EventType",
    "ExperimentResult",
    "GenerationEvent",
    "GovernanceError",
    "HandoffEvent",
    "HumanReviewEvent",
    # LLM gateway
    "LLMClient",
    "LLMJudge",
    "LLMResponse",
    # prompt registry
    "ManagedPrompt",
    # privacy
    "MaskingPolicy",
    "PolicyEvent",
    # prompt identity
    "PromptIdentity",
    "PromptRegistry",
    "Redactor",
    "RetrievalEvent",
    "RetrievedChunk",
    # scoring & evaluation
    "Score",
    "ScoreDataType",
    "ScoreSource",
    "Severity",
    "ToolInvocationEvent",
    # context
    "TraceContext",
    # tracing
    "Tracer",
    # configuration
    "WatcherConfig",
    "__version__",
    "add_item",
    "agent_dataset_name",
    "agent_prompt_name",
    "bind",
    "capture",
    "complete",
    "configure",
    "create_dataset",
    "create_prompt",
    "current_context",
    "decision",
    "evaluate",
    "fingerprint_text",
    "flush",
    "generation",
    "get_agent_prompt",
    "get_config",
    "get_items",
    "get_prompt",
    "get_registry",
    "get_trace_id",
    "get_tracer",
    "handoff",
    "identify_prompt",
    # instrumentation
    "instrument_litellm",
    "mask_payload",
    "mask_text",
    # decorators
    "observe",
    "observe_agent",
    "observe_tool",
    "policy",
    "record_score",
    "reset_config",
    "retrieval",
    "run_experiment",
    "run_prompt",
    "score",
    "span",
    "stream",
    "tool",
    "trace",
    "use_prompt",
]
