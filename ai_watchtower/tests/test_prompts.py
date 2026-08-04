"""
Prompt registry.

The behaviour that matters is not "can it fetch a prompt" — it is what happens
when it *cannot*. Prompts sit on the critical path of every request, so the
degradation ladder (fresh → registry → stale → fallback) is the contract, and
each rung is tested explicitly.
"""

from __future__ import annotations

import time

import pytest

from ai_watchtower.config import reset_config
from ai_watchtower.prompts import ManagedPrompt, PromptRegistry, get_registry


class FakePrompt:
    """Shaped like a Langfuse prompt object."""

    def __init__(self, prompt, version="1", labels=("production",), config=None):
        self.prompt = prompt
        self.version = version
        self.labels = labels
        self.config = config or {}


class FakeClient:
    def __init__(self, prompts=None, fail=False):
        self._prompts = prompts or {}
        self.fail = fail
        self.calls = 0

    def get_prompt(self, name, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("registry unreachable")
        if name not in self._prompts:
            raise RuntimeError(f"Prompt not found: {name}")
        return self._prompts[name]


@pytest.fixture(autouse=True)
def _clean():
    reset_config()
    yield
    reset_config()


class TestManagedPrompt:
    def test_compiles_placeholders(self):
        p = ManagedPrompt(name="p", template="Hello {{name}}, you have {{n}} cars")
        assert p.compile(name="Acme", n=322) == "Hello Acme, you have 322 cars"

    def test_tolerates_whitespace_in_placeholder(self):
        assert ManagedPrompt(name="p", template="{{ name }}").compile(name="X") == "X"

    def test_unknown_placeholder_survives(self):
        """A missing variable degrades the answer; it must not 500 the request,
        and the gap stays visible in the recorded prompt."""
        out = ManagedPrompt(name="p", template="{{a}} and {{b}}").compile(a="A")
        assert out == "A and {{b}}"

    def test_identity_fingerprints_the_template(self):
        """Not the compiled text — otherwise every request looks like a new version."""
        p = ManagedPrompt(name="p", template="Hi {{name}}")
        assert p.identity.fingerprint == ManagedPrompt(name="p", template="Hi {{name}}").identity.fingerprint

    def test_identity_carries_registration(self):
        assert ManagedPrompt(name="p", template="x", registered=True).identity.registered is True

    def test_metadata_includes_staleness(self):
        assert ManagedPrompt(name="p", template="x", stale=True).as_metadata()["prompt_stale"] is True


class TestResolution:
    def test_fetches_from_registry(self):
        client = FakeClient({"greeting": FakePrompt("Hello {{name}}", version="3")})
        p = PromptRegistry(client=client).get("greeting")
        assert p.template == "Hello {{name}}"
        assert p.version == "3"
        assert p.registered is True

    def test_chat_prompt_list_is_joined(self):
        """Chat prompts arrive as a message list; they must still fingerprint."""
        client = FakeClient({"chat": FakePrompt(
            [{"role": "system", "content": "You are helpful"},
             {"role": "user", "content": "{{q}}"}])})
        p = PromptRegistry(client=client).get("chat")
        assert "You are helpful" in p.template and "{{q}}" in p.template

    def test_missing_prompt_uses_fallback(self):
        p = PromptRegistry(client=FakeClient({})).get("nope", fallback="local default")
        assert p.template == "local default"
        assert p.registered is False   # a fallback is never "registered"

    def test_no_client_uses_fallback(self):
        p = PromptRegistry(client=None).get("nope", fallback="local")
        assert p.template == "local"

    def test_registry_failure_uses_fallback(self):
        p = PromptRegistry(client=FakeClient(fail=True)).get("x", fallback="local")
        assert p.template == "local"


class TestCaching:
    def test_second_call_is_cached(self):
        client = FakeClient({"g": FakePrompt("Hello")})
        registry = PromptRegistry(client=client)
        registry.get("g")
        registry.get("g")
        assert client.calls == 1

    def test_fallback_is_cached_too(self):
        """Otherwise a prompt missing from the registry costs a round-trip and
        a 404 on every single request."""
        client = FakeClient({})
        registry = PromptRegistry(client=client)
        registry.get("missing", fallback="x")
        registry.get("missing", fallback="x")
        assert client.calls == 1

    def test_ttl_expiry_refetches(self):
        client = FakeClient({"g": FakePrompt("Hello")})
        registry = PromptRegistry(client=client, ttl_seconds=0.01)
        registry.get("g")
        time.sleep(0.02)
        registry.get("g")
        assert client.calls == 2

    def test_invalidate_by_name(self):
        client = FakeClient({"g": FakePrompt("Hello")})
        registry = PromptRegistry(client=client)
        registry.get("g")
        registry.invalidate("g")
        registry.get("g")
        assert client.calls == 2

    def test_invalidate_all(self):
        client = FakeClient({"a": FakePrompt("A"), "b": FakePrompt("B")})
        registry = PromptRegistry(client=client)
        registry.get("a"); registry.get("b")
        registry.invalidate()
        registry.get("a"); registry.get("b")
        assert client.calls == 4

    def test_versions_cached_separately(self):
        client = FakeClient({"g": FakePrompt("Hello")})
        registry = PromptRegistry(client=client)
        registry.get("g", version="1")
        registry.get("g", version="2")
        assert client.calls == 2


class TestStaleFallback:
    def test_serves_stale_when_registry_dies(self):
        """The rung that keeps the product answering during an outage."""
        client = FakeClient({"g": FakePrompt("Hello v1")})
        registry = PromptRegistry(client=client, ttl_seconds=0.01)

        first = registry.get("g")
        assert first.stale is False

        client.fail = True
        time.sleep(0.02)                      # force a refresh attempt
        second = registry.get("g")

        assert second.template == "Hello v1"  # last good value
        assert second.stale is True           # flagged, not silently fresh
        assert second.registered is True      # it did come from the registry

    def test_stale_preferred_over_fallback(self):
        client = FakeClient({"g": FakePrompt("registry value")})
        registry = PromptRegistry(client=client, ttl_seconds=0.01)
        registry.get("g")
        client.fail = True
        time.sleep(0.02)
        assert registry.get("g", fallback="worse").template == "registry value"


class TestModuleLevel:
    def test_get_registry_is_singleton(self):
        assert get_registry() is get_registry()
