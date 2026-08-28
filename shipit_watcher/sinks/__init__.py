"""Sinks — pluggable destinations for observability events."""

from .base import ConsoleSink, FanOutSink, Sink

__all__ = [
    "ConsoleSink",
    "DashboardSink",
    "DjangoSink",
    "FanOutSink",
    "LangSmithOTLPSink",
    "LangfuseOTLPSink",
    "LangfuseSink",
    "OpenInferenceOTLPSink",
    "PhoenixOTLPSink",
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
    if name == "PhoenixOTLPSink":
        from .phoenix_sink import PhoenixOTLPSink
        return PhoenixOTLPSink
    if name == "LangSmithOTLPSink":
        from .langsmith_sink import LangSmithOTLPSink
        return LangSmithOTLPSink
    if name == "OpenInferenceOTLPSink":
        from .openinference_otel_sink import OpenInferenceOTLPSink
        return OpenInferenceOTLPSink
    if name == "DashboardSink":
        from .dashboard_sink import DashboardSink
        return DashboardSink
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
