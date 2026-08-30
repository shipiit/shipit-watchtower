from __future__ import annotations

from types import SimpleNamespace

import pytest

from shipit_watcher.backends import Backend
from shipit_watcher.config import configure, get_config, reset_config
from shipit_watcher.management import (
    DashboardManagementClient,
    LangSmithManagementClient,
    PhoenixManagementClient,
)
from shipit_watcher.prompts import PromptRegistry


@pytest.fixture(autouse=True)
def clean_config():
    reset_config()
    yield
    reset_config()


class PhoenixPrompts:
    def __init__(self):
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            messages=[{"role": "system", "content": "Hello {{name}}"}],
            id="pv-7", tags=["production"], config={"model": "gpt-test"},
        )


class PhoenixDatasets:
    def __init__(self):
        self.created = []
        self.added = []

    def get_dataset(self, dataset):
        if not self.created:
            raise LookupError(dataset)
        return SimpleNamespace(name=dataset, examples=[])

    def create_dataset(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(
            examples=[SimpleNamespace(id="ex-1")], name=kwargs["name"]
        )

    def add_examples_to_dataset(self, **kwargs):
        self.added.append(kwargs)
        return SimpleNamespace(examples=[SimpleNamespace(id="stable-1")])


class PhoenixClient:
    def __init__(self):
        self.prompts = PhoenixPrompts()
        self.datasets = PhoenixDatasets()


def test_phoenix_prompt_tag_is_the_management_selector():
    raw = PhoenixClient()
    client = PhoenixManagementClient(raw)
    client.get_prompt("support", label="production")
    assert raw.prompts.calls == [{
        "prompt_identifier": "support", "tag": "production"
    }]


def test_prompt_registry_understands_phoenix_message_shape():
    registry = PromptRegistry(client=PhoenixManagementClient(PhoenixClient()))
    prompt = registry.get("support", label="production")
    assert prompt.registered is True
    assert prompt.version == "pv-7"
    assert "Hello {{name}}" in prompt.template
    assert prompt.compile(name="Ada").endswith("Ada")


def test_phoenix_dataset_create_is_idempotent():
    raw = PhoenixClient()
    client = PhoenixManagementClient(raw)
    client.create_dataset("regressions", description="bad turns")
    client.create_dataset("regressions", description="bad turns")
    assert len(raw.datasets.created) == 1


def test_phoenix_stable_item_id_uses_diff_key():
    raw = PhoenixClient()
    client = PhoenixManagementClient(raw)
    result = client.create_dataset_item(
        dataset_name="regressions", input={"q": "x"},
        expected_output={"a": "y"}, metadata={}, id="stable-1",
    )
    assert result.id == "stable-1"
    sent = raw.datasets.added[0]
    assert sent["example_id_key"] == "example_id"
    assert sent["examples"][0]["example_id"] == "stable-1"


class LangSmithClient:
    def __init__(self):
        self.pulled = []
        self.datasets = {}
        self.examples = []

    def pull_prompt(self, identifier):
        self.pulled.append(identifier)
        return SimpleNamespace(pretty_repr=lambda: "Hello {name}")

    def has_dataset(self, dataset_name):
        return dataset_name in self.datasets

    def create_dataset(self, dataset_name, description=None, metadata=None):
        value = SimpleNamespace(id=f"ds-{dataset_name}", name=dataset_name)
        self.datasets[dataset_name] = value
        return value

    def read_dataset(self, dataset_name):
        return self.datasets[dataset_name]

    def create_example(self, **kwargs):
        self.examples.append(kwargs)
        return SimpleNamespace(id=kwargs.get("example_id", "generated"))

    def list_examples(self, dataset_id):
        return []


def test_langsmith_prompt_commit_or_tag_selector():
    raw = LangSmithClient()
    client = LangSmithManagementClient(raw)
    client.get_prompt("support", label="production")
    assert raw.pulled == ["support:production"]


def test_langsmith_dataset_and_example_mapping():
    raw = LangSmithClient()
    client = LangSmithManagementClient(raw)
    client.create_dataset("regressions")
    result = client.create_dataset_item(
        dataset_name="regressions", input="question", expected_output="answer",
        metadata={"source": "prod"}, id="00000000-0000-0000-0000-000000000001",
    )
    assert result.id.endswith("1")
    assert raw.examples[0]["inputs"] == {"input": "question"}
    assert raw.examples[0]["outputs"] == {"output": "answer"}


def test_management_backend_resolution_is_explicit(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    reset_config()
    assert get_config().resolved_management_backend == "langfuse"
    configure(management_backend="phoenix")
    assert get_config().resolved_management_backend == "phoenix"


def test_dashboard_is_the_management_store_when_no_vendor_is_configured():
    configure(dashboard_url="https://watcher.example")
    assert get_config().resolved_management_backend == "dashboard"


def test_dashboard_management_maps_prompts_datasets_and_experiment_links(monkeypatch):
    client = DashboardManagementClient("https://watcher.example", "secret")
    calls = []

    def request(path, payload=None):
        calls.append((path, payload))
        if path.startswith("/api/prompts/"):
            return {"id": "p1", "prompt": "Hello {{name}}", "version": 2,
                    "labels": ["production"], "config": {"model": "test"}}
        if path.startswith("/api/datasets/"):
            return {"id": "d1", "name": "regressions", "items": [{
                "id": "i1", "input": {"q": "x"}, "expected_output": {"a": "y"},
                "metadata": {},
            }]}
        if path == "/api/experiments":
            return {"id": "e1"}
        raise AssertionError(path)

    monkeypatch.setattr(client, "_request", request)
    prompt = client.get_prompt("support agent", label="production")
    dataset = client.get_dataset("regressions")
    result = dataset.items[0].link(
        None, "prompt-v2", trace_id="abc", run_description="candidate"
    )

    assert prompt.version == 2
    assert calls[0][0] == "/api/prompts/support%20agent?label=production"
    assert result == {"id": "e1"}
    assert calls[-1][1]["dataset_item_id"] == "i1"


def test_explicit_backend_bundle_is_the_authoritative_store():
    client = LangSmithManagementClient(LangSmithClient())
    configure(backends=(Backend(name="company-store", prompts=client, datasets=client),))
    from shipit_watcher.management import management_client

    assert get_config().resolved_management_backend == "company-store"
    assert management_client() is client
