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

from .backends import (
    Backend,
    BackendCapabilities,
    LangSmithBackend,
    PhoenixBackend,
)
from .budgets import (
    BudgetAction,
    BudgetExceeded,
    BudgetState,
    budget,
    current_budget,
    use_budget,
)
from .config import WatcherConfig, configure, get_config, reset_config
from .context import (
    TraceContext,
    bind,
    current_context,
    extract_trace_context,
    get_trace_id,
    inject_trace_context,
    use_prompt,
)
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
from .guardrails import GuardrailViolation, check_content, guard
from .identity import PromptIdentity, fingerprint_text, identify_prompt
from .llm import GovernanceError, LLMClient, LLMResponse, complete, stream
from .llm import run_prompt as run_prompt
from .masking import MaskingPolicy, Redactor, mask_payload, mask_text
from .pricing import ModelPrice, estimate_cost, get_model_price, set_model_price
from .prompts import (
    ManagedPrompt,
    PromptRegistry,
    agent_prompt_name,
    create_prompt,
    get_agent_prompt,
    get_prompt,
    get_registry,
)
from .replay import (
    TraceBundle,
    TraceBundleSink,
    compare_bundles,
    load_bundle,
    replay_bundle,
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
from .setup import BackendStatus, SetupReport, doctor, setup
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


def instrument_openai() -> bool:
    """Trace every OpenAI SDK call, including ones made inside libraries.

    Patched on the SDK's own classes rather than on a client instance, so a
    client constructed three layers down in a dependency is covered too.
    ``wt.setup()`` calls this automatically when the SDK is installed.
    """
    from .instrumentation.openai import instrument

    return instrument()


def instrument_anthropic() -> bool:
    """Trace every Anthropic SDK call, including streamed ones.

    ``wt.setup()`` calls this automatically when the SDK is installed.
    """
    from .instrumentation.anthropic import instrument

    return instrument()


def instrument_litellm(*, replace_langfuse_callback: bool = True) -> bool:
    """Take ownership of LiteLLM call tracing.

    Removes LiteLLM's built-in Langfuse callback so a call is traced once, by
    this SDK, inside the ambient trace — rather than twice, flat and
    unparented, with no tenant or prompt metadata.
    """
    from .instrumentation.litellm import instrument

    return instrument(replace_langfuse_callback=replace_langfuse_callback)


def langgraph_callback(tracer: Tracer | None = None):
    """Create a Watcher callback for LangGraph or LangChain."""
    from .instrumentation.langgraph import langgraph_callback as create_callback

    return create_callback(tracer)


def instrument_langgraph(graph: Any, tracer: Tracer | None = None):
    """Attach Watcher tracing to a compiled LangGraph runnable."""
    from .instrumentation.langgraph import instrument_langgraph as instrument

    return instrument(graph, tracer)


def flush() -> None:
    """Force delivery of buffered events. Call before a process exits."""
    get_tracer().flush()


__all__ = [
    "JUDGE_RUBRICS",
    "Backend",
    "BackendCapabilities",
    "BackendStatus",
    "BudgetAction",
    "BudgetExceeded",
    "BudgetState",
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
    "GuardrailViolation",
    "HandoffEvent",
    "HumanReviewEvent",
    # LLM gateway
    "LLMClient",
    "LLMJudge",
    "LLMResponse",
    "LangSmithBackend",
    # prompt registry
    "ManagedPrompt",
    # privacy
    "MaskingPolicy",
    "ModelPrice",
    "PhoenixBackend",
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
    "SetupReport",
    "Severity",
    "ToolInvocationEvent",
    "TraceBundle",
    "TraceBundleSink",
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
    "budget",
    "capture",
    "check_content",
    "compare_bundles",
    "complete",
    "configure",
    "create_dataset",
    "create_prompt",
    "current_budget",
    "current_context",
    "decision",
    "doctor",
    "estimate_cost",
    "evaluate",
    "extract_trace_context",
    "fingerprint_text",
    "flush",
    "generation",
    "get_agent_prompt",
    "get_config",
    "get_items",
    "get_model_price",
    "get_prompt",
    "get_registry",
    "get_trace_id",
    "get_tracer",
    "guard",
    "handoff",
    "identify_prompt",
    "inject_trace_context",
    "instrument_anthropic",
    "instrument_langgraph",
    # instrumentation
    "instrument_litellm",
    "instrument_openai",
    "langgraph_callback",
    "load_bundle",
    "mask_payload",
    "mask_text",
    # decorators
    "observe",
    "observe_agent",
    "observe_tool",
    "policy",
    "record_score",
    "replay_bundle",
    "reset_config",
    "retrieval",
    "run_experiment",
    "run_prompt",
    "score",
    "set_model_price",
    "setup",
    "span",
    "stream",
    "tool",
    "trace",
    "use_budget",
    "use_prompt",
]
