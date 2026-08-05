"""
Datasets and experiments.

The behaviour worth pinning is not "can it call the API" — it is that a
capture never breaks the request it was captured from, and that a run records
what actually happened including the items that failed.
"""

from __future__ import annotations

import pytest

import shipit_watcher as wt
from shipit_watcher import datasets as ds
from shipit_watcher import tracer as tracer_mod
from shipit_watcher.config import reset_config
from shipit_watcher.scoring import Evaluator, Score, ScoreSource
from shipit_watcher.tracer import Tracer


class _NullSink:
    """Accepts everything, records nothing. Enough to make a tracer active."""

    def start_trace(self, *a, **k): ...
    def end_trace(self, *a, **k): ...
    def event(self, *a, **k): ...
    def flush(self, *a, **k): ...


class FakeItem:
    def __init__(self, id, input=None, expected_output=None, metadata=None):
        self.id = id
        self.input = input
        self.expected_output = expected_output
        self.metadata = metadata or {}
        self.source_trace_id = ""
        self.source_observation_id = ""
        self.links = []

    def link(self, trace_or_observation, run_name, **kwargs):
        self.links.append((run_name, kwargs.get("trace_id")))


class FakeDataset:
    def __init__(self, name, items):
        self.name = name
        self.items = items


class FakeClient:
    def __init__(self, items=None, fail=False):
        self.datasets = {}
        self.created_items = []
        self.items = items or []
        self.fail = fail

    def create_dataset(self, name, description=None, metadata=None):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.datasets[name] = {"description": description}
        return object()

    def create_dataset_item(self, **kwargs):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.created_items.append(kwargs)
        return type("Item", (), {"id": f"item-{len(self.created_items)}"})()

    def get_dataset(self, name, **kwargs):
        if self.fail:
            raise RuntimeError("langfuse down")
        return FakeDataset(name, self.items)


@pytest.fixture(autouse=True)
def _clean():
    reset_config()
    wt.configure(service_name="test", environment="test",
                 langfuse_public_key="pk", langfuse_secret_key="sk",
                 langfuse_host="https://langfuse.example")
    yield
    reset_config()


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(ds, "_client", lambda: fake)
    monkeypatch.setattr(ds, "_flush_traces", lambda *a, **k: None)
    return fake


class TestCapture:
    def test_capture_carries_the_source_trace(self, client):
        """An example with no origin cannot be re-examined when it starts
        failing."""
        with wt.bind(trace_id="t" * 32, session_id="s1", user_id="a@b.com"):
            ds.capture("regressions", input="q", expected_output="a")

        sent = client.created_items[0]
        assert sent["source_trace_id"] == "t" * 32
        assert sent["metadata"]["session_id"] == "s1"
        assert sent["metadata"]["user_id"] == "a@b.com"

    def test_capture_creates_the_dataset_on_first_use(self, client):
        ds.capture("brand-new", input="q")
        assert "brand-new" in client.datasets

    def test_capture_never_raises_into_the_request(self, monkeypatch):
        """Capturing an example must not be the reason a user's answer fails."""
        monkeypatch.setattr(ds, "_client", lambda: FakeClient(fail=True))
        assert ds.capture("regressions", input="q") is None

    def test_no_credentials_is_a_no_op(self, monkeypatch):
        reset_config()
        wt.configure(service_name="test", langfuse_public_key="",
                     langfuse_secret_key="")
        assert ds.capture("regressions", input="q") is None
        assert ds.create_dataset("regressions") is False
        assert ds.get_items("regressions") == []

    def test_item_id_makes_the_write_idempotent(self, client):
        ds.add_item("d", input="q", item_id="stable-key")
        assert client.created_items[0]["id"] == "stable-key"

    def test_omitted_fields_are_not_sent(self, client):
        ds.add_item("d", input="q")
        assert "source_trace_id" not in client.created_items[0]
        assert "id" not in client.created_items[0]


class TestGetItems:
    def test_reads_items(self, monkeypatch):
        fake = FakeClient(items=[FakeItem("i1", input="q", expected_output="a")])
        monkeypatch.setattr(ds, "_client", lambda: fake)
        items = ds.get_items("d")
        assert items[0].id == "i1" and items[0].expected_output == "a"

    def test_missing_dataset_is_empty_not_an_error(self, monkeypatch):
        monkeypatch.setattr(ds, "_client", lambda: FakeClient(fail=True))
        assert ds.get_items("nope") == []


class ExactMatch(Evaluator):
    name = "exact_match"

    def evaluate(self, *, input, output, expected=None, **_):
        return Score(name=self.name, value=str(output) == str(expected),
                     source=ScoreSource.PROGRAMMATIC)


class TestRunExperiment:
    @pytest.fixture
    def two_items(self, monkeypatch):
        items = [FakeItem("i1", input="cars", expected_output="142"),
                 FakeItem("i2", input="drivers", expected_output="100")]
        fake = FakeClient(items=items)
        monkeypatch.setattr(ds, "_client", lambda: fake)
        monkeypatch.setattr(ds, "_flush_traces", lambda *a, **k: None)
        # An experiment links each result back to its trace, and a tracer with
        # no sink never opens one — so without this the run produces no trace
        # id, the link is skipped, and the test reads as a linking bug rather
        # than a missing fixture.
        monkeypatch.setattr(tracer_mod, "get_tracer", lambda: Tracer(sinks=[_NullSink()]))
        return items

    def test_runs_every_item(self, two_items):
        results = ds.run_experiment(
            "d", lambda item: "142" if item.input == "cars" else "100",
            run_name="v1")
        assert [r.item_id for r in results] == ["i1", "i2"]
        assert all(r.ok for r in results)

    def test_links_each_result_to_the_run(self, two_items):
        ds.run_experiment("d", lambda item: "x", run_name="v1")
        for item in two_items:
            run_name, trace_id = item.links[0]
            assert run_name == "v1"
            assert trace_id                      # a real trace, not a placeholder

    def test_evaluators_score_each_result(self, two_items):
        results = ds.run_experiment(
            "d", lambda item: "142" if item.input == "cars" else "wrong",
            run_name="v1", evaluators=[ExactMatch()])
        assert results[0].scores["exact_match"] is True
        assert results[1].scores["exact_match"] is False

    def test_one_failure_does_not_abort_the_batch(self, two_items):
        """Aborting would discard the results already gathered, and a task
        that fails on one input is itself a finding."""
        def flaky(item):
            if item.input == "cars":
                raise ValueError("boom")
            return "100"

        results = ds.run_experiment("d", flaky, run_name="v1")
        assert len(results) == 2
        assert results[0].ok is False and "boom" in results[0].error
        assert results[1].ok is True

    def test_failed_items_are_not_scored(self, two_items):
        def always_fails(item):
            raise RuntimeError("down")

        results = ds.run_experiment("d", always_fails, run_name="v1",
                                    evaluators=[ExactMatch()])
        assert all(r.scores == {} for r in results)

    def test_subset_can_be_run(self, two_items):
        subset = [ds.DatasetItem(id="i2")]
        results = ds.run_experiment("d", lambda i: "x", run_name="v1",
                                    items=subset)
        assert [r.item_id for r in results] == ["i2"]

    def test_no_client_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ds, "_client", lambda: None)
        assert ds.run_experiment("d", lambda i: "x", run_name="v1") == []

    def test_link_failure_does_not_lose_results(self, monkeypatch):
        class Unlinkable(FakeItem):
            def link(self, *a, **k):
                raise RuntimeError("404")

        fake = FakeClient(items=[Unlinkable("i1", input="q")])
        monkeypatch.setattr(ds, "_client", lambda: fake)
        monkeypatch.setattr(ds, "_flush_traces", lambda *a, **k: None)
        results = ds.run_experiment("d", lambda i: "answer", run_name="v1")
        assert results[0].output == "answer"


class TestDefaultDataset:
    def test_capture_uses_the_configured_dataset(self, client):
        wt.configure(service_name="test", dataset="from-config",
                     langfuse_public_key="pk", langfuse_secret_key="sk")
        with wt.bind(trace_id="t" * 32):
            ds.capture(input="q")
        assert client.created_items[0]["dataset_name"] == "from-config"

    def test_explicit_dataset_wins(self, client):
        wt.configure(service_name="test", dataset="from-config",
                     langfuse_public_key="pk", langfuse_secret_key="sk")
        with wt.bind(trace_id="t" * 32):
            ds.capture("explicit", input="q")
        assert client.created_items[0]["dataset_name"] == "explicit"

    def test_no_dataset_anywhere_is_a_no_op(self, client):
        """Silently writing to a dataset named "" would be worse than telling
        the caller nothing was captured."""
        wt.configure(service_name="test", dataset="",
                     langfuse_public_key="pk", langfuse_secret_key="sk")
        with wt.bind(trace_id="t" * 32):
            assert ds.capture(input="q") is None
        assert client.created_items == []


class TestAgentDatasetName:
    """One dataset per agent: a failing case for one agent tells you nothing
    about another, and a shared dataset makes every run an average of
    unrelated cases."""

    def test_slug_from_the_agent(self):
        class A:
            name = "Inbox Manager"
        assert ds.agent_dataset_name(A(), prefix="myapp-eval-") == "myapp-eval-inbox-manager"

    def test_missing_separator_is_added(self):
        """Forgetting the trailing dash is the obvious mistake; silently
        producing `myapp-evalinbox-manager` helps nobody."""
        assert ds.agent_dataset_name("Inbox Manager", prefix="myapp-eval") == \
            "myapp-eval-inbox-manager"

    def test_existing_separator_is_respected(self):
        for prefix in ("p-", "p_", "p/", "p:", "p."):
            assert ds.agent_dataset_name("Ops", prefix=prefix) == f"{prefix}ops"

    def test_prefix_defaults_to_config(self):
        wt.configure(service_name="test", dataset="cfg-eval-",
                     langfuse_public_key="pk", langfuse_secret_key="sk")
        assert ds.agent_dataset_name("Ops") == "cfg-eval-ops"

    def test_no_prefix_is_just_the_slug(self):
        wt.configure(service_name="test", dataset="",
                     langfuse_public_key="pk", langfuse_secret_key="sk")
        assert ds.agent_dataset_name("Ops") == "ops"

    def test_display_name_and_slug_agree(self):
        class A:
            slug = "inbox-manager"
        assert ds.agent_dataset_name(A(), prefix="e-") == \
            ds.agent_dataset_name("Inbox Manager", prefix="e-")
