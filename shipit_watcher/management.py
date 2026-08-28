"""Vendor adapters for prompts and datasets.

The public prompt/dataset APIs stay stable while the selected management
backend is chosen with WATCHER_MANAGEMENT_BACKEND. Tracing can still fan out
to all configured vendors; management has one authoritative store.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from types import SimpleNamespace
from typing import Any

from .config import get_config


def _mapping(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


class PhoenixManagementClient:
    def __init__(self, client: Any = None):
        if client is None:
            from phoenix.client import Client

            client = Client()
        self.client = client

    def get_prompt(self, name: str, **selectors: Any) -> Any:
        kwargs: dict[str, Any] = {"prompt_identifier": name}
        if selectors.get("version") is not None:
            kwargs["prompt_version_id"] = selectors["version"]
        elif selectors.get("label"):
            kwargs["tag"] = selectors["label"]
        return self.client.prompts.get(**kwargs)

    def create_prompt(self, **payload: Any) -> Any:
        from phoenix.client.types import PromptVersion

        template = payload["prompt"]
        messages = template if isinstance(template, list) else [
            {"role": "system", "content": str(template)}
        ]
        config = payload.get("config") or {}
        provider = str(config.get("provider") or "OPENAI").upper()
        version_type: Any = PromptVersion
        version = version_type(
            messages,
            model_name=config.get("model", "gpt-4.1-mini"),
            model_provider=provider,
        )
        prompt_metadata = {
            "watcher_config": {
                k: v for k, v in config.items() if k not in {"model", "provider"}
            },
            "watcher_labels": list(payload.get("labels") or ()),
        }
        return self.client.prompts.create(
            name=payload["name"], version=version, prompt_metadata=prompt_metadata
        )

    def create_dataset(self, name: str, description: str | None = None,
                       metadata: dict[str, Any] | None = None) -> Any:
        try:
            return self.client.datasets.get_dataset(dataset=name)
        except Exception:
            return self.client.datasets.create_dataset(
                name=name, dataset_description=description,
                inputs=[], outputs=[], metadata=[],
            )

    def create_dataset_item(self, **payload: Any) -> Any:
        add = getattr(self.client.datasets, "add_examples_to_dataset", None)
        if callable(add):
            row = {
                "input": payload.get("input"),
                "output": payload.get("expected_output"),
                "metadata": payload.get("metadata") or {},
            }
            kwargs: dict[str, Any] = {
                "dataset": {"name": payload["dataset_name"]},
                "examples": [row],
                "input_keys": ["input"],
                "output_keys": ["output"],
                "metadata_keys": ["metadata"],
            }
            if payload.get("id"):
                row["example_id"] = payload["id"]
                kwargs["example_id_key"] = "example_id"
            result = add(**kwargs)
        else:
            result = self.client.datasets.create_dataset(
                name=payload["dataset_name"],
                inputs=[payload.get("input")],
                outputs=[payload.get("expected_output")],
                metadata=[payload.get("metadata") or {}],
            )
        examples = getattr(result, "examples", None) or []
        return examples[-1] if examples else SimpleNamespace(id=payload.get("id", ""))

    def get_dataset(self, name: str) -> Any:
        raw = self.client.datasets.get_dataset(dataset=name)
        items = []
        for example in getattr(raw, "examples", []) or []:
            items.append(SimpleNamespace(
                id=str(_mapping(example, "id", "")),
                input=_mapping(example, "input"),
                expected_output=_mapping(example, "output"),
                metadata=dict(_mapping(example, "metadata", {}) or {}),
                source_trace_id="",
                source_observation_id="",
                link=lambda *args, **kwargs: None,
            ))
        return SimpleNamespace(items=items)

    def run_experiment(self, dataset: str, task: Any, **options: Any) -> Any:
        runner = getattr(self.client.experiments, "run_experiment", None)
        if not callable(runner):
            raise RuntimeError("installed Phoenix client does not support experiments")
        dataset_obj = self.client.datasets.get_dataset(dataset=dataset)
        if "run_name" in options:
            options["experiment_name"] = options.pop("run_name")
        if "description" in options:
            options["experiment_description"] = options.pop("description")
        if "metadata" in options:
            options["experiment_metadata"] = options.pop("metadata")
        return runner(dataset=dataset_obj, task=task, **options)

    def add_annotation(self, target_id: str, **annotation: Any) -> Any:
        payload = {
            "name": annotation.pop("name", "watcher.feedback"),
            "span_id": target_id,
            "annotator_kind": annotation.pop("annotator_kind", "HUMAN"),
            "result": annotation.pop("result", annotation),
        }
        return self.client.spans.log_span_annotations(span_annotations=[payload])


class LangSmithManagementClient:
    def __init__(self, client: Any = None):
        if client is None:
            from langsmith import Client

            client = Client()
        self.client = client

    def get_prompt(self, name: str, **selectors: Any) -> Any:
        selector = selectors.get("version") or selectors.get("label")
        identifier = f"{name}:{selector}" if selector else name
        return self.client.pull_prompt(identifier)

    def create_prompt(self, **payload: Any) -> Any:
        try:
            from langchain_core.prompts import ChatPromptTemplate
        except ImportError as exc:
            raise RuntimeError(
                "LangSmith prompt publishing requires langchain-core"
            ) from exc
        template = payload["prompt"]
        if isinstance(template, list):
            messages = [
                (m.get("role", "user"), m.get("content", ""))
                if isinstance(m, dict) else m for m in template
            ]
            prompt = ChatPromptTemplate.from_messages(messages)
        else:
            prompt = ChatPromptTemplate.from_template(str(template))
        url = self.client.push_prompt(
            payload["name"],
            object=prompt,
            commit_tags=list(payload.get("labels") or ()),
        )
        return SimpleNamespace(
            prompt=template, version=str(url).rstrip("/").rsplit("/", 1)[-1],
            labels=payload.get("labels") or (), config=payload.get("config") or {},
        )

    def create_dataset(self, name: str, description: str | None = None,
                       metadata: dict[str, Any] | None = None) -> Any:
        if self.client.has_dataset(dataset_name=name):
            return self.client.read_dataset(dataset_name=name)
        return self.client.create_dataset(
            dataset_name=name, description=description, metadata=metadata
        )

    @staticmethod
    def _object(value: Any, key: str) -> dict[str, Any] | None:
        if value is None:
            return None
        return value if isinstance(value, dict) else {key: value}

    def create_dataset_item(self, **payload: Any) -> Any:
        dataset = self.client.read_dataset(dataset_name=payload["dataset_name"])
        kwargs: dict[str, Any] = dict(
            inputs=self._object(payload.get("input"), "input") or {},
            outputs=self._object(payload.get("expected_output"), "output"),
            metadata=payload.get("metadata") or {},
            dataset_id=dataset.id,
        )
        if payload.get("id"):
            kwargs["example_id"] = payload["id"]
        return self.client.create_example(**kwargs)

    def get_dataset(self, name: str) -> Any:
        dataset = self.client.read_dataset(dataset_name=name)
        items = []
        for example in self.client.list_examples(dataset_id=dataset.id):
            inputs = getattr(example, "inputs", None)
            outputs = getattr(example, "outputs", None)
            items.append(SimpleNamespace(
                id=str(getattr(example, "id", "")),
                input=inputs,
                expected_output=outputs,
                metadata=dict(getattr(example, "metadata", {}) or {}),
                source_trace_id="",
                source_observation_id="",
                link=lambda *args, **kwargs: None,
            ))
        return SimpleNamespace(items=items)

    def run_experiment(self, dataset: str, task: Any, **options: Any) -> Any:
        from langsmith import evaluate

        if "run_name" in options:
            options["experiment_prefix"] = options.pop("run_name")
        return evaluate(task, data=dataset, client=self.client, **options)

    def add_annotation(self, target_id: str, **annotation: Any) -> Any:
        payload = {
            "run_id": target_id,
            "key": annotation.pop("name", "watcher.feedback"),
            **annotation,
        }
        return self.client.create_feedback(**payload)


class DashboardManagementClient:
    """Prompt and dataset capability backed by Watcher's own dashboard."""

    def __init__(self, url: str | None = None, token: str | None = None):
        config = get_config()
        self.url = (url or config.dashboard_url).rstrip("/")
        self.token = config.dashboard_token if token is None else token

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, default=str).encode()
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(f"{self.url}{path}", data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=10.0) as response:
            return json.loads(response.read().decode())

    @staticmethod
    def _prompt(value: dict[str, Any]) -> Any:
        return SimpleNamespace(
            prompt=value.get("prompt"), template=value.get("prompt"),
            version=value.get("version"), labels=value.get("labels") or [],
            tags=value.get("tags") or [], config=value.get("config") or {},
            id=value.get("id"),
        )

    def get_prompt(self, name: str, **selectors: Any) -> Any:
        query = urllib.parse.urlencode({
            key: value for key, value in selectors.items() if value is not None
        })
        value = self._request(
            f"/api/prompts/{urllib.parse.quote(name, safe='')}"
            + (f"?{query}" if query else "")
        )
        return self._prompt(value)

    def create_prompt(self, **payload: Any) -> Any:
        return self._prompt(self._request("/api/prompts", payload))

    def create_dataset(self, name: str, description: str | None = None,
                       metadata: dict[str, Any] | None = None) -> Any:
        value = self._request("/api/datasets", {
            "name": name, "description": description, "metadata": metadata or {},
        })
        return SimpleNamespace(**value)

    def create_dataset_item(self, **payload: Any) -> Any:
        return SimpleNamespace(**self._request("/api/dataset-items", payload))

    def get_dataset(self, name: str) -> Any:
        value = self._request(f"/api/datasets/{urllib.parse.quote(name, safe='')}")
        items = []
        for raw in value.get("items", []):
            item_id = str(raw.get("id", ""))

            def link(_observation_id: Any, run_name: str, *, trace_id: str = "",
                     run_description: str | None = None,
                     run_metadata: dict[str, Any] | None = None,
                     _item_id: str = item_id) -> Any:
                return self._request("/api/experiments", {
                    "dataset_item_id": _item_id, "run_name": run_name,
                    "trace_id": trace_id, "description": run_description,
                    "metadata": run_metadata or {},
                })

            items.append(SimpleNamespace(
                id=item_id, input=raw.get("input"),
                expected_output=raw.get("expected_output"),
                metadata=raw.get("metadata") or {},
                source_trace_id=raw.get("source_trace_id") or "",
                source_observation_id=raw.get("source_observation_id") or "",
                link=link,
            ))
        return SimpleNamespace(items=items, **{
            key: value[key] for key in ("id", "name", "description", "metadata")
            if key in value
        })


def management_client() -> Any:
    config = get_config()
    backend = config.resolved_management_backend
    for bundle in config.backends:
        name = str(getattr(bundle, "name", "custom")).lower()
        if name == backend:
            return getattr(bundle, "prompts", None) or getattr(bundle, "datasets", None)
    if backend == "phoenix":
        return PhoenixManagementClient()
    if backend == "langsmith":
        return LangSmithManagementClient()
    if backend == "dashboard":
        return DashboardManagementClient()
    return None
