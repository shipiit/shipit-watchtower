"""Sinks — pluggable destinations for observability events."""

from .base import ConsoleSink, FanOutSink, Sink

__all__ = [
    "ConsoleSink",
    "DjangoSink",
    "FanOutSink",
    "LangfuseOTLPSink",
    "LangfuseSink",
    "Sink",
]


def __getattr__(name):
    """Import the heavy sinks on first use.

    Each one pulls in a client library — Langfuse, Django ORM — that a process
    using only the console sink has no reason to load.
    """
    if name == "LangfuseSink":
        from .langfuse_sink import LangfuseSink
        return LangfuseSink
    if name == "LangfuseOTLPSink":
        from .langfuse_otel_sink import LangfuseOTLPSink
        return LangfuseOTLPSink
    if name == "DjangoSink":
        from .django_sink import DjangoSink
        return DjangoSink
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
