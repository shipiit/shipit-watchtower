"""The command line.

These commands are what somebody runs when the dashboard is empty and they do
not know why, so the assertions here are mostly about whether the output says
something useful — and about the exit codes, which a container health check
consumes.
"""

from __future__ import annotations

import pytest

from shipit_watcher.cli import main
from shipit_watcher.config import reset_config


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


def test_no_subcommand_exits_with_a_usage_error():
    with pytest.raises(SystemExit) as raised:
        main([])
    assert raised.value.code == 2


def test_config_prints_no_secret_values(monkeypatch, capsys):
    """The whole point of this command is that it is safe to paste."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-secret")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-very-secret")
    monkeypatch.setenv("WATCHER_DASHBOARD_TOKEN", "token-secret")
    reset_config()

    assert main(["config"]) == 0
    output = capsys.readouterr().out
    assert "secret" not in output
    # Credentials are reported as booleans, never as values.
    assert '"langfuse": true' in output
    assert '"dashboard": false' in output


def test_doctor_fails_when_nothing_is_configured(monkeypatch, capsys):
    """A health check has to fail on a deployment that records nothing."""
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                 "PHOENIX_COLLECTOR_ENDPOINT", "LANGSMITH_API_KEY",
                 "WATCHER_DASHBOARD_URL"):
        monkeypatch.delenv(name, raising=False)
    reset_config()

    assert main(["doctor"]) == 1
    output = capsys.readouterr().out
    # And it has to say what to do about it, rather than failing silently.
    assert "no backend configured" in output
    assert "WATCHER_DASHBOARD_URL" in output


def test_doctor_passes_with_a_backend(monkeypatch, capsys):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    reset_config()

    assert main(["doctor"]) == 0
    assert "langfuse" in capsys.readouterr().out


def test_backends_is_an_alias_for_doctor(monkeypatch, capsys):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    reset_config()
    assert main(["backends"]) == 0
    assert "langfuse" in capsys.readouterr().out


def test_init_prints_a_template_that_names_the_next_step(capsys):
    assert main(["init"]) == 0
    output = capsys.readouterr().out
    assert "WATCHER_DASHBOARD_URL" in output
    assert "http://localhost:3000" in output
    assert "wt.setup" in output
    assert "watcher connect" in output


def test_connect_refuses_when_there_is_nothing_to_connect_to(monkeypatch, capsys):
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                 "PHOENIX_COLLECTOR_ENDPOINT", "LANGSMITH_API_KEY",
                 "WATCHER_DASHBOARD_URL"):
        monkeypatch.delenv(name, raising=False)
    reset_config()

    assert main(["connect"]) == 1
    assert "watcher init" in capsys.readouterr().out


def test_connect_reports_a_failed_delivery(monkeypatch, capsys):
    """A token can be present, well formed, and wrong.

    Configuration checks call that healthy; only sending something finds it.
    """
    import shipit_watcher.tracer as tracer_module
    from shipit_watcher.tracer import Tracer

    monkeypatch.setenv("WATCHER_DASHBOARD_URL", "https://watcher.example")
    monkeypatch.setenv("WATCHER_DASHBOARD_TOKEN", "wrong")
    reset_config()

    class Refusing:
        def __init__(self) -> None:
            self.delivery_stats = {"delivered": 0, "failed": 1, "dropped": 0}

        def start_trace(self, *args, **kwargs) -> None: ...
        def end_trace(self, *args, **kwargs) -> None: ...
        def record(self, *args, **kwargs) -> None: ...
        def flush(self) -> None: ...

    previous, tracer_module._tracer = tracer_module._tracer, Tracer(sinks=[Refusing()])
    try:
        assert main(["connect"]) == 1
    finally:
        tracer_module._tracer = previous
    assert "failed" in capsys.readouterr().out


def test_connect_confirms_a_delivery(monkeypatch, capsys):
    import shipit_watcher.tracer as tracer_module
    from shipit_watcher.tracer import Tracer

    monkeypatch.setenv("WATCHER_DASHBOARD_URL", "https://watcher.example")
    monkeypatch.setenv("WATCHER_DASHBOARD_TOKEN", "right")
    reset_config()

    class Accepting:
        def __init__(self) -> None:
            self.delivery_stats = {"delivered": 0, "failed": 0, "dropped": 0}

        def start_trace(self, *args, **kwargs) -> None: ...
        def end_trace(self, *args, **kwargs) -> None:
            self.delivery_stats = {"delivered": 1, "failed": 0, "dropped": 0}
        def record(self, *args, **kwargs) -> None: ...
        def flush(self) -> None: ...

    previous, tracer_module._tracer = tracer_module._tracer, Tracer(sinks=[Accepting()])
    try:
        assert main(["connect"]) == 0
    finally:
        tracer_module._tracer = previous
    assert "watcher.connect" in capsys.readouterr().out
