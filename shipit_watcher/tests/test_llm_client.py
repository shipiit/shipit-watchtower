from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from shipit_watcher.config import WatcherConfig, reset_config
from shipit_watcher.identity import PromptIdentity
from shipit_watcher.llm import GovernanceError, LLMClient
from shipit_watcher.tracer import Tracer


class Collector:
    def __init__(self):
        self.events = []

    def start_trace(self, *args):
        pass

    def end_trace(self, *args):
        pass

    def record(self, event, context):
        self.events.append(event)

    def flush(self):
        pass


def response(text="hello", *, prompt_tokens=4, completion_tokens=2, cost=0.01):
    message = SimpleNamespace(content=text, tool_calls=[])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(
        choices=[choice],
        usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        _hidden_params={"response_cost": cost},
    )


@pytest.fixture(autouse=True)
def clean():
    reset_config()
    yield
    reset_config()


def install_fake(monkeypatch, completion):
    module = types.ModuleType("litellm")
    module.completion = completion
    module.completion_cost = lambda **kwargs: 0.0
    monkeypatch.setitem(sys.modules, "litellm", module)
    return module


def test_complete_records_usage_cost_prompt_and_suppression(monkeypatch):
    calls = []
    install_fake(monkeypatch, lambda **kwargs: calls.append(kwargs) or response())
    collector = Collector()
    tracer = Tracer(sinks=[collector])
    monkeypatch.setattr("shipit_watcher.llm.get_tracer", lambda: tracer)
    identity = PromptIdentity(
        name="support", version="7", fingerprint="abc", registered=True
    )

    with tracer.trace("turn"):
        result = LLMClient("gpt-test").complete(
            [{"role": "user", "content": "hi"}], prompt=identity
        )

    assert result.text == "hello"
    assert result.total_tokens == 6
    assert result.total_cost == 0.01
    assert calls[0]["metadata"]["_watcher_managed_generation"] is True
    assert calls[0]["metadata"]["watcher_prompt"]["prompt_version"] == "7"
    generation = next(event for event in collector.events if event.name == "llm.completion")
    assert generation.total_cost == 0.01


def test_nonretryable_error_moves_directly_to_fallback(monkeypatch):
    class AuthenticationError(Exception):
        pass

    models = []

    def completion(**kwargs):
        models.append(kwargs["model"])
        if len(models) == 1:
            raise AuthenticationError("bad primary credentials")
        return response()

    install_fake(monkeypatch, completion)
    tracer = Tracer(sinks=[Collector()])
    monkeypatch.setattr("shipit_watcher.llm.get_tracer", lambda: tracer)
    with tracer.trace("turn"):
        result = LLMClient(
            "primary", max_retries=5, fallback_models=["fallback"]
        ).complete([])
    assert result.model == "fallback"
    assert models == ["primary", "fallback"]


def test_enforced_unregistered_prompt_blocks_before_provider(monkeypatch):
    calls = []
    install_fake(monkeypatch, lambda **kwargs: calls.append(kwargs) or response())
    collector = Collector()
    tracer = Tracer(
        sinks=[collector], config=WatcherConfig(governance_mode="enforce")
    )
    monkeypatch.setattr("shipit_watcher.llm.get_tracer", lambda: tracer)
    monkeypatch.setattr("shipit_watcher.llm.get_config", lambda: tracer.config)
    with tracer.trace("turn"), pytest.raises(GovernanceError):
        LLMClient("model").complete(
            [], prompt=PromptIdentity(name="draft", fingerprint="abc")
        )
    assert calls == []
    assert any(event.name == "policy.prompt_registry" for event in collector.events)


def test_stream_stays_open_and_records_final_usage(monkeypatch):
    def completion(**kwargs):
        assert kwargs["stream"] is True
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="hel"))], usage=None
        )
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))],
            usage={"prompt_tokens": 3, "completion_tokens": 2},
            _hidden_params={"response_cost": 0.02},
        )

    install_fake(monkeypatch, completion)
    collector = Collector()
    tracer = Tracer(sinks=[collector])
    monkeypatch.setattr("shipit_watcher.llm.get_tracer", lambda: tracer)
    with tracer.trace("turn"):
        chunks = list(LLMClient("model").stream([]))
    assert chunks == ["hel", "lo"]
    generation = next(event for event in collector.events if event.name == "llm.stream")
    assert generation.output == "hello"
    assert generation.total_tokens == 5
    assert generation.total_cost == 0.02


def test_gateway_alias_routes_through_openai_compatible_provider(monkeypatch):
    calls = []
    install_fake(monkeypatch, lambda **kwargs: calls.append(kwargs) or response())
    tracer = Tracer(sinks=[Collector()])
    monkeypatch.setattr("shipit_watcher.llm.get_tracer", lambda: tracer)
    with tracer.trace("turn"):
        LLMClient("private-alias", api_base="https://gateway.test").complete([])
    assert calls[0]["model"] == "openai/private-alias"
