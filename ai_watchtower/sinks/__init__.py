"""Sinks — pluggable destinations for observability events."""

from .base import ConsoleSink, FanOutSink, Sink

__all__ = ["Sink", "FanOutSink", "ConsoleSink"]
