"""OpenAI and Anthropic SDK instrumentation.

The SDKs are not installed here, so the tests stand up module trees with the
same shapes the real ones expose. That is the point of patching at the class
level: what is being verified is that Watcher finds and wraps
``<resource>.create``, whatever object happens to provide it.
"""

from __future__ import annotations

import importlib.machinery
import sys
import types

import pytest

from shipit_watcher.config import configure, reset_config
from shipit_watcher.tracer import Tracer


class Collector:
    def __init__(self) -> None:
        self.events: list = []

    def start_trace(self, *args, **kwargs) -> None: ...
    def end_trace(self, *args, **kwargs) -> None: ...
    def record(self, event, context) -> None:
        self.events.append(event)
    def flush(self) -> None: ...


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, None)
    sys.modules[name] = module
    return module


class _Usage(dict):
    """Usage as a mapping, the shape both SDKs' dict-mode responses use."""


def _openai_response(prompt=10, completion=5, cached=0):
    message = types.SimpleNamespace(content="the answer", tool_calls=None)
    choice = types.SimpleNamespace(message=message, finish_reason="stop")
    usage = _Usage(prompt_tokens=prompt, completion_tokens=completion)
    if cached:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return types.SimpleNamespace(choices=[choice], usage=usage)


@pytest.fixture
def fake_openai(monkeypatch):
    saved = {k: v for k, v in sys.modules.items() if k.startswith("openai")}
    for key in list(sys.modules):
        if key.startswith("openai"):
            del sys.modules[key]

    _module("openai")
    _module("openai.resources")
    _module("openai.resources.chat")
    completions = _module("openai.resources.chat.completions")
    responses = _module("openai.resources.responses")
    embeddings = _module("openai.resources.embeddings")

    calls: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("stream"):
                return iter(kwargs["_chunks"])
            return _openai_response(**kwargs.get("_usage", {}))

    class AsyncCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return _openai_response()

    completions.Completions = Completions
    completions.AsyncCompletions = AsyncCompletions
    def _stub(name: str) -> type:
        return type(name, (), {"create": lambda self, **kw: None})

    responses.Responses = _stub("Responses")
    responses.AsyncResponses = _stub("AsyncResponses")
    embeddings.Embeddings = _stub("Embeddings")
    embeddings.AsyncEmbeddings = _stub("AsyncEmbeddings")

    from shipit_watcher.instrumentation import openai as integration

    integration.uninstrument()
    yield types.SimpleNamespace(Completions=Completions,
                                AsyncCompletions=AsyncCompletions, calls=calls)
    integration.uninstrument()
    for key in list(sys.modules):
        if key.startswith("openai"):
            del sys.modules[key]
    sys.modules.update(saved)


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    configure(sample_rate=1.0)
    yield
    reset_config()


def test_an_openai_call_is_traced_with_usage_and_cost(fake_openai):
    from shipit_watcher.instrumentation.openai import instrument

    assert instrument() is True
    sink = Collector()
    tracer = Tracer(sinks=[sink])
    import shipit_watcher.tracer as tracer_module

    previous, tracer_module._tracer = tracer_module._tracer, tracer
    try:
        with tracer.trace("request"):
            fake_openai.Completions().create(
                model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        tracer_module._tracer = previous

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.model == "gpt-4o"
    assert event.provider == "openai"
    assert (event.prompt_tokens, event.completion_tokens) == (10, 5)
    # OpenAI reports usage and never price; without the pricing table every
    # call would land in a cost report as $0.00.
    assert event.total_cost == pytest.approx(10 / 1e6 * 2.5 + 5 / 1e6 * 10.0)
    assert event.output == "the answer"


def test_cached_tokens_are_billed_at_the_cached_rate(fake_openai):
    from shipit_watcher.instrumentation.openai import instrument

    instrument()
    sink = Collector()
    tracer = Tracer(sinks=[sink])
    import shipit_watcher.tracer as tracer_module

    previous, tracer_module._tracer = tracer_module._tracer, tracer
    try:
        with tracer.trace("request"):
            fake_openai.Completions().create(
                model="gpt-4o", messages=[], _usage={"prompt": 1000, "cached": 800},
            )
    finally:
        tracer_module._tracer = previous

    event = sink.events[0]
    assert event.metadata["cached_tokens"] == 800
    # 200 fresh at 2.50 + 800 cached at 1.25, not 1000 at 2.50.
    assert event.total_cost == pytest.approx((200 * 2.5 + 800 * 1.25 + 5 * 10.0) / 1e6)


def test_a_streamed_call_records_time_to_first_token(fake_openai):
    from shipit_watcher.instrumentation.openai import instrument

    instrument()
    sink = Collector()
    tracer = Tracer(sinks=[sink])
    import shipit_watcher.tracer as tracer_module

    def chunk(text=None, usage=None):
        delta = types.SimpleNamespace(content=text)
        choice = types.SimpleNamespace(delta=delta)
        return types.SimpleNamespace(choices=[choice], usage=usage)

    chunks = [
        chunk("Hel"), chunk("lo"),
        types.SimpleNamespace(
            choices=[], usage=_Usage(prompt_tokens=7, completion_tokens=2),
        ),
    ]

    previous, tracer_module._tracer = tracer_module._tracer, tracer
    try:
        with tracer.trace("request"):
            stream = fake_openai.Completions().create(
                model="gpt-4o", messages=[], stream=True, _chunks=chunks,
            )
            assert sink.events == []          # the span stays open for the stream
            received = list(stream)
    finally:
        tracer_module._tracer = previous

    assert len(received) == 3
    event = sink.events[0]
    assert event.output == "Hello"
    assert event.prompt_tokens == 7
    assert event.metadata["streamed_chunks"] == 3
    assert "time_to_first_token_ms" in event.metadata


def test_a_failing_call_is_recorded_then_re_raised(fake_openai):
    from shipit_watcher.instrumentation import openai as integration

    def explode(self, **kwargs):
        raise RuntimeError("rate limited")

    # Installed before instrumenting, so the wrapper wraps *this*.
    fake_openai.Completions.create = explode
    integration.instrument()

    sink = Collector()
    tracer = Tracer(sinks=[sink])
    import shipit_watcher.tracer as tracer_module

    previous, tracer_module._tracer = tracer_module._tracer, tracer
    try:
        with tracer.trace("request"), pytest.raises(RuntimeError, match="rate limited"):
            fake_openai.Completions().create(model="gpt-4o", messages=[])
    finally:
        tracer_module._tracer = previous

    assert sink.events[0].severity.value == "ERROR"
    assert "rate limited" in sink.events[0].status_message


def test_instrumenting_twice_does_not_double_wrap(fake_openai):
    from shipit_watcher.instrumentation import openai as integration

    integration.instrument()
    wrapped = fake_openai.Completions.create
    integration.instrument()
    assert fake_openai.Completions.create is wrapped


def test_uninstrument_restores_the_sdk(fake_openai):
    from shipit_watcher.instrumentation import openai as integration

    original = fake_openai.Completions.create
    integration.instrument()
    assert fake_openai.Completions.create is not original
    integration.uninstrument()
    assert fake_openai.Completions.create is original


# ── Anthropic ────────────────────────────────────────────────────────────────

def _anthropic_message(input_tokens=10, output_tokens=5, cached=0, created=0):
    usage = types.SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_input_tokens=cached, cache_creation_input_tokens=created,
    )
    block = types.SimpleNamespace(type="text", text="the answer")
    return types.SimpleNamespace(content=[block], usage=usage, stop_reason="end_turn")


@pytest.fixture
def fake_anthropic():
    saved = {k: v for k, v in sys.modules.items() if k.startswith("anthropic")}
    for key in list(sys.modules):
        if key.startswith("anthropic"):
            del sys.modules[key]

    _module("anthropic")
    _module("anthropic.resources")
    messages = _module("anthropic.resources.messages")

    class Messages:
        def create(self, **kwargs):
            if kwargs.get("stream"):
                return iter(kwargs["_chunks"])
            return _anthropic_message(**kwargs.get("_usage", {}))

    class AsyncMessages:
        async def create(self, **kwargs):
            return _anthropic_message()

    messages.Messages = Messages
    messages.AsyncMessages = AsyncMessages

    from shipit_watcher.instrumentation import anthropic as integration

    integration.uninstrument()
    yield types.SimpleNamespace(Messages=Messages)
    integration.uninstrument()
    for key in list(sys.modules):
        if key.startswith("anthropic"):
            del sys.modules[key]
    sys.modules.update(saved)


def test_an_anthropic_call_is_traced(fake_anthropic):
    from shipit_watcher.instrumentation.anthropic import instrument

    assert instrument() is True
    sink = Collector()
    tracer = Tracer(sinks=[sink])
    import shipit_watcher.tracer as tracer_module

    previous, tracer_module._tracer = tracer_module._tracer, tracer
    try:
        with tracer.trace("request"):
            fake_anthropic.Messages().create(
                model="claude-sonnet-4-5",
                system="be brief",
                messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        tracer_module._tracer = previous

    event = sink.events[0]
    assert event.provider == "anthropic"
    assert (event.prompt_tokens, event.completion_tokens) == (10, 5)
    assert event.output == "the answer"
    assert event.metadata["stop_reason"] == "end_turn"
    # The system prompt is part of what produced the answer.
    assert event.input["system"] == "be brief"


def test_cache_reads_and_writes_are_priced_differently(fake_anthropic):
    """A cached read is ~10% of input; a cache *write* costs a premium.

    Counting either as ordinary input misstates the bill in opposite
    directions, so they are kept apart.
    """
    from shipit_watcher.instrumentation.anthropic import instrument

    instrument()
    sink = Collector()
    tracer = Tracer(sinks=[sink])
    import shipit_watcher.tracer as tracer_module

    previous, tracer_module._tracer = tracer_module._tracer, tracer
    try:
        with tracer.trace("request"):
            fake_anthropic.Messages().create(
                model="claude-sonnet-4-5", messages=[],
                _usage={"input_tokens": 100, "cached": 900, "created": 50},
            )
    finally:
        tracer_module._tracer = previous

    event = sink.events[0]
    assert event.metadata["cached_tokens"] == 900
    # Total input is normalised to include the cached share: 100 + 50 + 900.
    assert event.prompt_tokens == 1050
    # 150 billed as input (100 fresh + 50 cache-write), 900 at the cached rate.
    assert event.total_cost == pytest.approx((150 * 3.0 + 900 * 0.3 + 5 * 15.0) / 1e6)


def test_setup_instruments_every_installed_sdk(fake_openai, fake_anthropic):
    """`wt.setup()` is meant to be the whole integration."""
    from shipit_watcher.setup import setup

    report = setup(service_name="my-app", register_shutdown=False,
                   dashboard_url="https://watcher.example")
    assert {"openai", "anthropic"} <= report.instrumented
