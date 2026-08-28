"""LangGraph/LangChain callback instrumentation without a hard dependency."""

from __future__ import annotations

import contextlib
import threading
import time
from contextlib import AbstractContextManager
from typing import Any

from ..context import TraceContext, current_context
from ..events import Event, EventType, GenerationEvent, Severity, ToolInvocationEvent
from ..tracer import Tracer, get_tracer

try:  # pragma: no cover - optional dependency boundary
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover

    class BaseCallbackHandler:  # type: ignore[no-redef]
        """Enough of LangChain's handler contract for dependency-free imports."""

        raise_error = False
        run_inline = True


def _identifier(value: Any) -> str:
    return str(value).replace("-", "")


def _name(serialized: Any, fallback: str, kwargs: dict[str, Any]) -> str:
    explicit = kwargs.get("name")
    if explicit:
        return str(explicit)
    if isinstance(serialized, dict):
        return str(serialized.get("name") or (serialized.get("id") or [fallback])[-1])
    return fallback


def _usage_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return getattr(value, "usage_metadata", None) or {}


def _usage(response: Any) -> tuple[int, int]:
    output = getattr(response, "llm_output", None) or {}
    usage = output.get("token_usage") or output.get("usage") or {}
    if not usage:
        generations = getattr(response, "generations", None) or []
        first = generations[0][0] if generations and generations[0] else None
        message = getattr(first, "message", None) or first
        usage = _usage_mapping(message)
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
    return int(prompt or 0), int(completion or 0)


def _trace_fields(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Translate common LangChain/LangGraph identity keys to Watcher context."""
    metadata = dict(kwargs.get("metadata") or {})
    configurable = kwargs.get("configurable") or metadata.get("configurable") or {}
    if isinstance(configurable, dict):
        metadata = {**configurable, **metadata}

    def first(*keys: str) -> Any:
        return next((metadata[key] for key in keys if metadata.get(key) is not None), None)

    return {
        "session_id": first(
            "session_id", "thread_id", "conversation_id", "langfuse_session_id"
        ),
        "user_id": first("user_id", "langfuse_user_id"),
        "company_id": first("company_id", "tenant_id", "organization_id"),
        "cost_center": first("cost_center"),
        "channel": first("channel"),
        "tags": [str(tag) for tag in (kwargs.get("tags") or [])],
        "metadata": metadata,
    }


class WatcherLangGraphCallback(BaseCallbackHandler):
    """Capture a complete graph run, with or without an ambient Watcher trace."""

    #: How many started-but-never-finished runs to retain before evicting the
    #: oldest. A cancelled or interrupted graph never fires its `on_*_end`
    #: hook, so without a bound these dicts grow for the life of the process,
    #: each entry holding the run's full input payload.
    MAX_PENDING_RUNS = 2048

    def __init__(self, tracer: Tracer | None = None) -> None:
        self.tracer = tracer or get_tracer()
        self._runs: dict[str, tuple[Event, TraceContext]] = {}
        self._owned_traces: dict[
            str, tuple[AbstractContextManager[TraceContext], TraceContext]
        ] = {}
        self._lock = threading.Lock()

    def _open_root_trace(
        self,
        serialized: Any,
        inputs: Any,
        run_id: Any,
        kwargs: dict[str, Any],
    ) -> None:
        if current_context().trace_id is not None:
            return
        manager = self.tracer.trace(
            _name(serialized, "langgraph.run", kwargs),
            input=inputs,
            **_trace_fields(kwargs),
        )
        context = manager.__enter__()
        with self._lock:
            self._owned_traces[_identifier(run_id)] = (manager, context)

    def _close_root_trace(
        self, run_id: Any, output: Any, error: BaseException | None
    ) -> None:
        with self._lock:
            owned = self._owned_traces.pop(_identifier(run_id), None)
        if not owned:
            return
        manager, context = owned
        context.set_output(
            {"error": f"{type(error).__name__}: {error}"} if error else output
        )
        if error is None:
            manager.__exit__(None, None, None)
            return
        # Hand the tracer the real exception so the root span is marked ERROR.
        # Closing with (None, None, None) took the success path, so a graph
        # that blew up produced a root carrying an error *payload* with an OK
        # status — and status-based alerting never saw it.
        # The tracer re-raises what it is handed, and that exception already
        # belongs to the framework's own error path — raising it again from a
        # callback would replace the graph's error with ours.
        with contextlib.suppress(BaseException):
            manager.__exit__(type(error), error, error.__traceback__)

    def _start(
        self,
        run_id: Any,
        parent_run_id: Any,
        event: Event,
        inputs: Any,
        kwargs: dict[str, Any],
    ) -> None:
        parent_key = _identifier(parent_run_id) if parent_run_id else None
        with self._lock:
            parent = self._runs.get(parent_key) if parent_key else None
        context = parent[1] if parent else current_context()
        event.id = _identifier(run_id)
        event.parent_id = _identifier(parent_run_id) if parent_run_id else context.parent_id
        event.input = inputs
        event.started_at = time.time()
        event.metadata.update(kwargs.get("metadata") or {})
        event.tags.extend(str(tag) for tag in (kwargs.get("tags") or []))
        with self._lock:
            while len(self._runs) >= self.MAX_PENDING_RUNS:
                self._runs.pop(next(iter(self._runs)), None)
            self._runs[event.id] = (event, context)

    def _finish(
        self, run_id: Any, output: Any = None, error: BaseException | None = None
    ) -> None:
        with self._lock:
            pending = self._runs.pop(_identifier(run_id), None)
        if not pending:
            return
        event, context = pending
        event.finish(
            output=output,
            severity=Severity.ERROR if error else None,
            status_message=f"{type(error).__name__}: {error}" if error else "",
        )
        if isinstance(event, ToolInvocationEvent) and error:
            event.succeeded = False
            event.error = str(error)
        self.tracer.record_event(event, context)

    def on_chain_start(
        self,
        serialized: Any,
        inputs: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        if parent_run_id is None:
            self._open_root_trace(serialized, inputs, run_id, kwargs)
        metadata = kwargs.get("metadata") or {}
        kind = EventType.AGENT if metadata.get("langgraph_node") else EventType.CHAIN
        self._start(
            run_id,
            parent_run_id,
            Event(name=_name(serialized, "chain", kwargs), type=kind),
            inputs,
            kwargs,
        )

    def on_chain_end(self, outputs: Any, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, outputs)
        self._close_root_trace(run_id, outputs, None)

    def on_chain_error(self, error: BaseException, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, error=error)
        self._close_root_trace(run_id, None, error)

    def on_tool_start(
        self,
        serialized: Any,
        input_str: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        name = _name(serialized, "tool", kwargs)
        self._start(
            run_id,
            parent_run_id,
            ToolInvocationEvent(name=f"tool.{name}", tool_name=name, arguments=input_str),
            input_str,
            kwargs,
        )

    def on_tool_end(self, output: Any, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, output)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, error=error)

    def on_retriever_start(
        self,
        serialized: Any,
        query: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        self._start(
            run_id,
            parent_run_id,
            Event(name=_name(serialized, "retriever", kwargs), type=EventType.RETRIEVAL),
            query,
            kwargs,
        )

    def on_retriever_end(self, documents: Any, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, documents)

    def on_retriever_error(self, error: BaseException, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, error=error)

    def on_llm_start(
        self,
        serialized: Any,
        prompts: list[str],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        invocation = kwargs.get("invocation_params") or {}
        event = GenerationEvent(
            name=_name(serialized, "llm", kwargs),
            model=str(invocation.get("model") or invocation.get("model_name") or ""),
            provider=str(invocation.get("provider") or ""),
        )
        self._start(run_id, parent_run_id, event, prompts, kwargs)

    def on_chat_model_start(
        self,
        serialized: Any,
        messages: list[list[Any]],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Capture chat-native inputs without flattening role/content messages."""
        self.on_llm_start(
            serialized,
            messages,  # type: ignore[arg-type]
            run_id=run_id,
            parent_run_id=parent_run_id,
            **kwargs,
        )

    def on_llm_new_token(
        self,
        token: str | list[str | dict[str, Any]],
        *,
        chunk: Any = None,
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        **_: Any,
    ) -> None:
        """Record streaming responsiveness and volume without retaining content."""
        with self._lock:
            pending = self._runs.get(_identifier(run_id))
            if not pending or not isinstance(pending[0], GenerationEvent):
                return
            event = pending[0]
            count = int(event.metadata.get("streamed_chunks", 0)) + 1
            event.metadata["streamed_chunks"] = count
            if count == 1:
                event.metadata["first_token_latency_ms"] = max(
                    0, int((time.time() - event.started_at) * 1000)
                )

    def on_llm_end(self, response: Any, *, run_id: Any, **_: Any) -> None:
        with self._lock:
            pending = self._runs.get(_identifier(run_id))
        if pending and isinstance(pending[0], GenerationEvent):
            pending[0].prompt_tokens, pending[0].completion_tokens = _usage(response)
        self._finish(run_id, response)

    def on_llm_error(self, error: BaseException, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, error=error)


def langgraph_callback(tracer: Tracer | None = None) -> WatcherLangGraphCallback:
    """Return a callback for ``graph.invoke(..., config={"callbacks": [...]})``."""
    return WatcherLangGraphCallback(tracer)


def instrument_langgraph(graph: Any, tracer: Tracer | None = None) -> Any:
    """Attach Watcher to a compiled LangGraph runnable and return the runnable."""
    callback = langgraph_callback(tracer)
    if not hasattr(graph, "with_config"):
        raise TypeError(
            "instrument_langgraph() expects a compiled LangGraph/LangChain runnable"
        )
    return graph.with_config(callbacks=[callback])
