"""Backend capability bundles.

Tracing, prompts, datasets and feedback evolve independently in each vendor.
Representing them as optional capabilities keeps Watcher vendor-neutral and
lets a deployment use, for example, Phoenix for traces and LangSmith for
experiments without installing fake implementations for either side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .sinks.base import Sink

__all__ = [
    "AnnotationBackend",
    "Backend",
    "BackendCapabilities",
    "DatasetBackend",
    "ExperimentBackend",
    "LangSmithBackend",
    "PhoenixBackend",
    "PromptBackend",
    "ScoreBackend",
    "TraceBackend",
]


@runtime_checkable
class TraceBackend(Sink, Protocol):
    """A backend capable of receiving trace lifecycle events."""


@runtime_checkable
class ScoreBackend(Protocol):
    def record_score(self, score: Any) -> None: ...


@runtime_checkable
class PromptBackend(Protocol):
    def get_prompt(self, name: str, **selectors: Any) -> Any: ...
    def create_prompt(self, **payload: Any) -> Any: ...


@runtime_checkable
class DatasetBackend(Protocol):
    def create_dataset(
        self,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any: ...
    def create_dataset_item(self, **payload: Any) -> Any: ...
    def get_dataset(self, name: str) -> Any: ...


@runtime_checkable
class ExperimentBackend(Protocol):
    def run_experiment(self, dataset: str, task: Any, **options: Any) -> Any: ...


@runtime_checkable
class AnnotationBackend(Protocol):
    def add_annotation(self, target_id: str, **annotation: Any) -> Any: ...


@dataclass(frozen=True)
class BackendCapabilities:
    traces: bool = False
    scores: bool = False
    prompts: bool = False
    datasets: bool = False
    experiments: bool = False
    annotations: bool = False


@dataclass
class Backend:
    name: str
    trace: TraceBackend | None = None
    prompts: PromptBackend | None = None
    datasets: DatasetBackend | None = None
    experiments: ExperimentBackend | None = None
    annotations: AnnotationBackend | None = None

    @property
    def capabilities(self) -> BackendCapabilities:
        trace = self.trace
        return BackendCapabilities(
            traces=trace is not None,
            scores=isinstance(trace, ScoreBackend),
            prompts=self.prompts is not None,
            datasets=self.datasets is not None,
            experiments=self.experiments is not None,
            annotations=self.annotations is not None,
        )


class PhoenixBackend(Backend):
    @classmethod
    def from_env(cls) -> PhoenixBackend:
        from .management import PhoenixManagementClient
        from .sinks.phoenix_sink import PhoenixOTLPSink

        try:
            management = PhoenixManagementClient()
        except Exception:
            management = None
        return cls(
            name="phoenix", trace=PhoenixOTLPSink(), prompts=management,
            datasets=management, experiments=management, annotations=management,
        )


class LangSmithBackend(Backend):
    @classmethod
    def from_env(cls) -> LangSmithBackend:
        from .management import LangSmithManagementClient
        from .sinks.langsmith_sink import LangSmithOTLPSink

        try:
            management = LangSmithManagementClient()
        except Exception:
            management = None
        return cls(
            name="langsmith", trace=LangSmithOTLPSink(), prompts=management,
            datasets=management, experiments=management, annotations=management,
        )
