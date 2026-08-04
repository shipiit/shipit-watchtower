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

from shipit_watcher.config import reset_config
from shipit_watcher.prompts import (
    ManagedPrompt, PromptRegistry, agent_prompt_name, get_registry,
)


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


class RecordingClient(FakeClient):
    """Adds the write side of the Langfuse prompt API."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.created = []

    def create_prompt(self, **kwargs):
        if self.fail:
            raise RuntimeError("registry unreachable")
        self.created.append(kwargs)
        template = kwargs["prompt"]
        created = FakePrompt(
            template,
            version=str(len(self.created)),
            labels=tuple(kwargs.get("labels") or ()),
            config=kwargs.get("config"),
        )
        self._prompts[kwargs["name"]] = created
        return created


class TestCreate:
    def test_publishes_and_returns_the_version(self):
        client = RecordingClient({})
        p = PromptRegistry(client=client).create("greeting", "Hello {{name}}")
        assert p.version == "1"
        assert p.registered is True
        assert client.created[0]["type"] == "text"

    def test_chat_prompt_type_is_inferred(self):
        client = RecordingClient({})
        PromptRegistry(client=client).create(
            "chat", [{"role": "system", "content": "hi"}]
        )
        assert client.created[0]["type"] == "chat"

    def test_chat_prompt_still_fingerprints(self):
        client = RecordingClient({})
        p = PromptRegistry(client=client).create(
            "chat", [{"role": "system", "content": "You triage"},
                     {"role": "user", "content": "{{q}}"}]
        )
        assert "You triage" in p.template and "{{q}}" in p.template
        assert p.identity.fingerprint

    def test_labels_and_config_are_passed_through(self):
        client = RecordingClient({})
        PromptRegistry(client=client).create(
            "g", "x", labels=["staging"], tags=["t"],
            config={"model": "m"}, commit_message="why",
        )
        sent = client.created[0]
        assert sent["labels"] == ["staging"]
        assert sent["tags"] == ["t"]
        assert sent["config"] == {"model": "m"}
        assert sent["commit_message"] == "why"

    def test_publishing_invalidates_the_cache(self):
        """Otherwise a release is invisible until the TTL expires — including
        a cached *fallback* from before the prompt existed."""
        client = RecordingClient({})
        registry = PromptRegistry(client=client)

        stale = registry.get("g", fallback="local default")
        assert stale.registered is False

        registry.create("g", "from the registry")
        assert registry.get("g").template == "from the registry"

    def test_write_failure_raises(self):
        """Unlike get(), which degrades: a failed publish is a release that
        did not happen and must not be reported as success."""
        registry = PromptRegistry(client=RecordingClient({}, fail=True))
        with pytest.raises(RuntimeError):
            registry.create("g", "x")

    def test_no_client_raises(self):
        with pytest.raises(RuntimeError, match="LANGFUSE_PUBLIC_KEY"):
            PromptRegistry(client=None).create("g", "x")


class TestAgentPrompts:
    def test_name_from_slug(self):
        class A:
            slug = "billing-expert"
        assert agent_prompt_name(A()) == "agent:billing-expert"

    def test_display_name_is_slugified(self):
        """`"Support Assistant"` and `"support-assistant"` must not become
        two prompts, or half the fleet silently runs an older version."""
        class A:
            name = "Support Assistant"
        assert agent_prompt_name(A()) == "agent:support-assistant"
        assert agent_prompt_name("support-assistant") == "agent:support-assistant"

    def test_slug_wins_over_display_name(self):
        class A:
            slug = "canonical"
            name = "Something Else"
        assert agent_prompt_name(A()) == "agent:canonical"

    def test_punctuation_collapses(self):
        assert agent_prompt_name("Support  //  Ops!!") == "agent:support-ops"

    def test_empty_agent_degrades_to_the_prefix(self):
        assert agent_prompt_name("") == "agent"

    def test_resolves_per_agent(self):
        client = FakeClient({"agent:billing-expert": FakePrompt("Fuel {{q}}", version="4")})
        import shipit_watcher.prompts as pmod

        previous, pmod._registry = pmod._registry, PromptRegistry(client=client)
        try:
            p = pmod.get_agent_prompt("Billing Expert")
            assert p.version == "4" and p.registered is True
        finally:
            pmod._registry = previous

    def test_unregistered_agent_uses_its_own_system_prompt(self):
        """Adoption is incremental: agents without a registry entry keep
        working off the database, and `registered` says which is which."""
        import shipit_watcher.prompts as pmod

        previous, pmod._registry = pmod._registry, PromptRegistry(client=FakeClient({}))
        try:
            p = pmod.get_agent_prompt("New Agent", fallback="db system prompt")
            assert p.template == "db system prompt"
            assert p.registered is False
        finally:
            pmod._registry = previous


class TestStaleOnlyMeansStale:
    def test_recached_fallback_is_not_stale(self):
        """A prompt nobody has registered is re-served from cache on every
        request. That is the steady state, not a Langfuse outage — reporting
        it as staleness sends people hunting for a problem that isn't there."""
        registry = PromptRegistry(client=FakeClient({}), ttl_seconds=0.01)
        registry.get("missing", fallback="local")
        time.sleep(0.02)
        again = registry.get("missing", fallback="local")
        assert again.template == "local"
        assert again.stale is False
        assert again.registered is False

    def test_registry_value_still_goes_stale(self):
        client = FakeClient({"g": FakePrompt("from registry")})
        registry = PromptRegistry(client=client, ttl_seconds=0.01)
        registry.get("g")
        client.fail = True
        time.sleep(0.02)
        assert registry.get("g").stale is True
