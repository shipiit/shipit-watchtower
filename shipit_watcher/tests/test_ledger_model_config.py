"""The ledger works in more than one Django project.

The sink imported ``agent.models.llm_call`` by path, so the local ledger
only ever worked in the application it was extracted from. Anywhere else
the ImportError was caught by the sink's own except and became a warning
in a log — an empty ledger that looked like an idle one.
"""

from __future__ import annotations

import pytest

from shipit_watcher.config import WatcherConfig, reset_config
from shipit_watcher.sinks.django_sink import _ledger_model


@pytest.fixture(autouse=True)
def _clean():
    reset_config()
    yield
    reset_config()


class TestTheDefaults:
    def test_the_original_path_is_unchanged(self, monkeypatch) -> None:
        """Existing deployments must not need to set anything."""
        monkeypatch.delenv("WATCHER_LEDGER_MODEL", raising=False)
        assert WatcherConfig().ledger_model == "agent.LLMCallRecord"

    def test_the_event_table_has_its_own_setting(self, monkeypatch) -> None:
        monkeypatch.delenv("WATCHER_LEDGER_EVENT_MODEL", raising=False)
        assert WatcherConfig().ledger_event_model == "agent.TraceEventRecord"

    def test_the_environment_overrides_it(self, monkeypatch) -> None:
        monkeypatch.setenv("WATCHER_LEDGER_MODEL", "agents.LLMCallRecord")
        assert WatcherConfig().ledger_model == "agents.LLMCallRecord"


class TestResolving:
    def test_a_label_without_a_dot_is_refused_by_name(self) -> None:
        """The message has to name the value — the failure it replaced
        said only that something went wrong."""
        with pytest.raises(ValueError, match="LLMCallRecord"):
            _ledger_model("LLMCallRecord")

    def test_an_empty_label_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _ledger_model("")
