"""
Datasets — turn real traffic into a regression suite.

The Langfuse UI has an "Add to dataset" button on every trace. It is the right
idea and the wrong ergonomics: the interesting cases are the ones nobody was
watching, and by the time you notice a bad answer you are scrolling for it.

This module makes capture a line of code instead of a click. The cases that
matter — a low score, a guardrail refusal, a tool that errored, a turn a user
gave a thumbs-down — can be captured *as they happen*, from inside the trace
that produced them.

Three things, in the order you need them:

**Capture.** :func:`capture` adds the current turn to a dataset, linked back
to the trace it came from, so the row keeps its provenance.

**Replay.** :func:`run_experiment` runs every item through a function and
records the results as a named run, so two prompt versions can be compared on
the same inputs rather than on impressions.

**Score.** Evaluators passed to :func:`run_experiment` attach scores to each
result, which is what makes the comparison a number instead of a vibe.

Everything degrades to a no-op without Langfuse credentials, and nothing here
raises into a caller's request path — capturing an example must never be the
reason a user's answer fails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .config import get_config
from .context import current_context

logger = logging.getLogger(__name__)

__all__ = [
    "DatasetItem",
    "ExperimentResult",
    "capture",
    "create_dataset",
    "add_item",
    "get_items",
    "run_experiment",
]


@dataclass
class DatasetItem:
    """One example: what went in, what should come out, where it came from."""

    id: str = ""
    input: Any = None
    expected_output: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: The trace this was captured from. Provenance matters — an example with
    #: no origin cannot be re-examined when it starts failing.
    source_trace_id: str = ""
    source_observation_id: str = ""


@dataclass
class ExperimentResult:
    """One item's outcome in a run."""

    item_id: str = ""
    output: Any = None
    scores: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    trace_id: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _flush_traces(settle_seconds: float = 2.0) -> None:
    """Ship buffered traces and give ingestion a moment to land them."""
    import time

    from .tracer import get_tracer

    try:
        get_tracer().flush()
    except Exception:
        logger.debug("watcher: flush before linking failed", exc_info=True)
    time.sleep(settle_seconds)


def _client() -> Any:
    config = get_config()
    if not config.has_langfuse_credentials:
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=config.langfuse_public_key,
            secret_key=config.langfuse_secret_key,
            host=config.langfuse_host,
        )
    except Exception:
        logger.warning("watcher: no Langfuse client for datasets", exc_info=True)
        return None


def create_dataset(name: str, *, description: str = "",
                   metadata: Optional[Dict[str, Any]] = None) -> bool:
    """Create a dataset. Existing datasets are left alone.

    Returns whether the dataset exists afterwards, so a startup hook can call
    it unconditionally.
    """
    client = _client()
    if client is None:
        return False
    try:
        client.create_dataset(name=name, description=description or None,
                              metadata=metadata or {})
        return True
    except Exception:
        logger.warning("watcher: could not create dataset %r", name, exc_info=True)
        return False


def add_item(dataset: str, *, input: Any = None, expected_output: Any = None,
             metadata: Optional[Dict[str, Any]] = None,
             source_trace_id: str = "", source_observation_id: str = "",
             item_id: str = "") -> Optional[str]:
    """Add one example to a dataset. Returns its id, or None on failure.

    ``item_id`` makes the write idempotent: re-adding the same id updates the
    row instead of creating a duplicate, which matters when capture runs on
    a code path that can retry.
    """
    client = _client()
    if client is None:
        return None
    try:
        payload: Dict[str, Any] = {
            "dataset_name": dataset,
            "input": input,
            "expected_output": expected_output,
            "metadata": metadata or {},
        }
        if source_trace_id:
            payload["source_trace_id"] = source_trace_id
        if source_observation_id:
            payload["source_observation_id"] = source_observation_id
        if item_id:
            payload["id"] = item_id
        return getattr(client.create_dataset_item(**payload), "id", None)
    except Exception:
        logger.warning("watcher: could not add an item to %r", dataset, exc_info=True)
        return None


def capture(dataset: str = "", *, input: Any = None, expected_output: Any = None,
            metadata: Optional[Dict[str, Any]] = None,
            create: bool = True) -> Optional[str]:
    """Capture the *current turn* into a dataset.

    Called inside a trace, the origin fills itself in::

        with wt.trace("agent.turn", user_id=user.email) as ctx:
            answer = run_agent(question)
            ctx.set_output({"answer": answer})

            if user_said_it_was_wrong:
                wt.capture("regressions", input=question,
                           expected_output=None,
                           metadata={"reported_by": user.email})

    That is the whole point: the examples worth keeping are the ones that just
    went wrong, and they are cheapest to keep at the moment they do.

    ``create=True`` makes the dataset on first use, so a capture path never
    fails because nobody clicked "New dataset" first.

    The dataset name defaults to ``WATCHER_DATASET``, so the call at the point
    of failure does not have to repeat it and switching datasets is a config
    change rather than a sweep through every call site.
    """
    dataset = dataset or get_config().dataset
    if not dataset:
        logger.warning(
            "watcher: capture() needs a dataset — pass one, or set "
            "WATCHER_DATASET / configure(dataset=...)"
        )
        return None

    context = current_context()
    if create:
        create_dataset(dataset)
    return add_item(
        dataset,
        input=input,
        expected_output=expected_output,
        metadata={
            **(metadata or {}),
            **({"session_id": context.session_id} if context.session_id else {}),
            **({"user_id": context.user_id} if context.user_id else {}),
            **({"company_id": context.company_id} if context.company_id else {}),
        },
        source_trace_id=context.trace_id or "",
    )


def get_items(dataset: str) -> List[DatasetItem]:
    """Every item in a dataset. Empty list if it does not exist."""
    client = _client()
    if client is None:
        return []
    try:
        raw = client.get_dataset(dataset)
    except Exception:
        logger.warning("watcher: could not read dataset %r", dataset, exc_info=True)
        return []

    items: List[DatasetItem] = []
    for item in getattr(raw, "items", []) or []:
        items.append(DatasetItem(
            id=str(getattr(item, "id", "")),
            input=getattr(item, "input", None),
            expected_output=getattr(item, "expected_output", None),
            metadata=dict(getattr(item, "metadata", {}) or {}),
            source_trace_id=str(getattr(item, "source_trace_id", "") or ""),
            source_observation_id=str(getattr(item, "source_observation_id", "") or ""),
        ))
    return items


def run_experiment(
    dataset: str,
    task: Callable[[DatasetItem], Any],
    *,
    run_name: str,
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    evaluators: Sequence[Any] = (),
    items: Optional[Iterable[DatasetItem]] = None,
) -> List[ExperimentResult]:
    """Run every item through *task* and record the results as a named run.

        wt.run_experiment(
            "regressions",
            task=lambda item: agent.answer(item.input),
            run_name="prompt-v7",
            evaluators=[wt.LLMJudge(judge, criterion="faithfulness")],
        )

    Each item gets its own trace, linked to the dataset row, so a run is
    comparable to the run before it — which is the difference between "the new
    prompt feels better" and "the new prompt scores 0.82 against 0.71 on the
    same 40 cases".

    An item whose task raises is recorded as a failure and the run continues.
    Aborting the batch on the first error would throw away the results already
    gathered, and a task that fails on one input is itself a finding.
    """
    from .scoring import evaluate
    from .tracer import get_tracer

    client = _client()
    if client is None:
        logger.warning("watcher: no Langfuse client; experiment %r skipped", run_name)
        return []

    try:
        dataset_client = client.get_dataset(dataset)
    except Exception:
        logger.warning("watcher: could not read dataset %r", dataset, exc_info=True)
        return []

    raw_items = list(getattr(dataset_client, "items", []) or [])
    wanted = {item.id for item in items} if items is not None else None

    tracer = get_tracer()
    results: List[ExperimentResult] = []
    pending: List[Any] = []

    for raw in raw_items:
        item_id = str(getattr(raw, "id", ""))
        if wanted is not None and item_id not in wanted:
            continue

        item = DatasetItem(
            id=item_id,
            input=getattr(raw, "input", None),
            expected_output=getattr(raw, "expected_output", None),
            metadata=dict(getattr(raw, "metadata", {}) or {}),
        )
        result = ExperimentResult(item_id=item_id)

        with tracer.trace(f"experiment.{run_name}", input=item.input,
                          tags=["experiment", f"run:{run_name}"],
                          metadata={"dataset": dataset, "item_id": item_id}) as context:
            result.trace_id = context.trace_id or ""
            try:
                result.output = task(item)
                context.set_output(result.output)
            except Exception as exc:
                result.error = f"{type(exc).__name__}: {exc}"[:500]
                context.set_output({"error": result.error})
                logger.warning("watcher: experiment item %s failed: %s",
                               item_id, result.error)

            if evaluators and result.ok:
                for score in evaluate(evaluators, input=item.input,
                                      output=result.output,
                                      expected=item.expected_output):
                    result.scores[score.name] = score.value

        pending.append((raw, item_id, result))

        results.append(result)

    # Linking is deferred to the end of the run for two reasons. The trace has
    # to exist server-side before it can be referenced, and OTLP export is
    # asynchronous — linking inline reliably 404s on a trace that is still in
    # flight. Flushing once and linking afterwards is both correct and one
    # round trip instead of N.
    _flush_traces()
    for raw, item_id, result in pending:
        if not result.trace_id:
            continue
        try:
            # `trace_id=` by keyword, not positionally. A bare string in the
            # first argument is read as an *observation* id — a legacy shape
            # that fails with "Observation ... not found" for a trace id.
            raw.link(None, run_name, trace_id=result.trace_id,
                     run_description=description or None,
                     run_metadata=metadata or {})
        except Exception:
            logger.warning("watcher: could not link item %s to run %r",
                           item_id, run_name, exc_info=True)

    passed = sum(1 for r in results if r.ok)
    logger.info("watcher: experiment %r — %d/%d completed",
                run_name, passed, len(results))
    return results
