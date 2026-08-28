"""
A delegating proxy that keeps a generation span open for a whole stream.

Shared by the provider integrations because the mistake it avoids is the same
everywhere: return the provider's stream object and close the span when the
method returns, and you record a span of nearly zero milliseconds that never
sees the token counts, the answer, or an error raised mid-stream.

Providers differ only in how a chunk carries usage and text, so those two are
injected.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from ..events import GenerationEvent, Severity
from ..pricing import estimate_cost
from ..tracer import get_tracer

logger = logging.getLogger(__name__)

__all__ = ["TracedStream", "fail_event", "record_event"]


def record_event(event: GenerationEvent) -> None:
    try:
        get_tracer().record_event(event)
    except Exception:
        logger.warning("watcher: could not record a model call", exc_info=True)


def fail_event(event: GenerationEvent, exc: BaseException) -> None:
    event.severity = Severity.ERROR
    event.status_message = f"{type(exc).__name__}: {exc}"[:500]
    event.finish()
    record_event(event)


class TracedStream:
    """Delegating proxy that keeps the span open for the whole stream.

    Returning the raw stream and closing the span immediately — the obvious
    implementation — records a span of nearly zero milliseconds that never
    sees the token counts, the answer, or an error raised mid-stream.

    Everything not related to iteration is forwarded, so callers that use the
    SDK's own stream helpers keep working.
    """

    def __init__(self, stream: Any, event: GenerationEvent, started: float, *,
                 usage_of: Callable[[Any], tuple[int, int, int]],
                 text_of: Callable[[Any], str]):
        self._stream = stream
        self._event = event
        self._started = started
        self._usage_of = usage_of
        self._text_of = text_of
        self._chunks = 0
        self._text: list[str] = []
        self._done = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    # -- sync ------------------------------------------------------------
    def __iter__(self):
        try:
            for chunk in self._stream:
                self._observe(chunk)
                yield chunk
        except BaseException as exc:
            self._fail(exc)
            raise
        else:
            self._close()

    # -- async -----------------------------------------------------------
    async def __aiter__(self):
        try:
            async for chunk in self._stream:
                self._observe(chunk)
                yield chunk
        except BaseException as exc:
            self._fail(exc)
            raise
        else:
            self._close()

    def __enter__(self):
        self._stream.__enter__()
        return self

    def __exit__(self, *exc_info):
        self._close()
        return self._stream.__exit__(*exc_info)

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc_info):
        self._close()
        return await self._stream.__aexit__(*exc_info)

    # -- internals -------------------------------------------------------
    def _observe(self, chunk: Any) -> None:
        if self._chunks == 0:
            # For a streamed answer this is the latency a user actually feels;
            # total duration mostly measures how long the answer was.
            self._event.metadata["time_to_first_token_ms"] = int(
                (time.time() - self._started) * 1000
            )
        self._chunks += 1
        prompt_tokens, completion_tokens, cached = self._usage_of(chunk)
        if prompt_tokens or completion_tokens:
            self._event.prompt_tokens = prompt_tokens or self._event.prompt_tokens
            self._event.completion_tokens = completion_tokens or self._event.completion_tokens
            if cached:
                self._event.metadata["cached_tokens"] = cached
        piece = self._text_of(chunk)
        if piece:
            self._text.append(piece)

    def _close(self) -> None:
        if self._done:
            return
        self._done = True
        self._event.output = "".join(self._text)
        self._event.metadata["streamed_chunks"] = self._chunks
        self._event.metadata["latency_ms"] = int((time.time() - self._started) * 1000)
        self._event.total_cost = estimate_cost(
            self._event.model, self._event.prompt_tokens, self._event.completion_tokens,
            int(self._event.metadata.get("cached_tokens", 0) or 0),
        )
        self._event.finish()
        record_event(self._event)

    def _fail(self, exc: BaseException) -> None:
        if self._done:
            return
        self._done = True
        self._event.output = "".join(self._text)
        self._event.metadata["streamed_chunks"] = self._chunks
        fail_event(self._event, exc)

    def close(self) -> None:
        # An abandoned stream is still a call that was made and paid for.
        self._close()
        closer = getattr(self._stream, "close", None)
        if callable(closer):
            closer()
