"""Dependency-free HTTP request tracing for ASGI and WSGI applications.

The middleware records request shape and response status, never request bodies,
cookies, authorisation values, or arbitrary headers. Identity headers are
opt-in because trusting them is an application/security decision.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from typing import Any

from .context import extract_trace_context, inject_trace_context
from .tracer import Tracer, get_tracer

__all__ = ["WatcherASGIMiddleware", "WatcherWSGIMiddleware"]

ContextResolver = Callable[[Mapping[str, Any], Mapping[str, str]], Mapping[str, Any] | None]
NameResolver = Callable[[Mapping[str, Any]], str]

_CONTEXT_FIELDS = {"user_id", "session_id", "company_id", "cost_center", "channel"}
_RESOLVER_FIELDS = _CONTEXT_FIELDS | {"metadata", "tags"}
_DEFAULT_EXCLUDED = ("/health", "/healthz", "/ready", "/readiness", "/metrics")
logger = logging.getLogger(__name__)
_PATH_IDENTIFIER = re.compile(
    r"^(?:\d+|[0-9a-f]{16,}|[0-9a-f]{8}-[0-9a-f-]{27,})$", re.IGNORECASE
)


def _normalise_path(path: str) -> str:
    """Keep default trace names low-cardinality without framework imports."""
    return "/".join(
        "{id}" if _PATH_IDENTIFIER.fullmatch(part) else part for part in path.split("/")
    )


def _default_name(scope: Mapping[str, Any]) -> str:
    method = str(scope.get("method", "HTTP")).upper()
    route = scope.get("route")
    path = getattr(route, "path", None) or _normalise_path(str(scope.get("path") or "/"))
    return f"http.{method.lower()} {path}"


def _identity_context(
    headers: Mapping[str, str], identity_headers: Mapping[str, str]
) -> dict[str, str]:
    return {
        field: headers[header.lower()]
        for field, header in identity_headers.items()
        if field in _CONTEXT_FIELDS and header.lower() in headers
    }


def _context_fields(
    scope: Mapping[str, Any],
    headers: Mapping[str, str],
    identity_headers: Mapping[str, str],
    resolver: ContextResolver | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = extract_trace_context(dict(headers))
    fields.update(_identity_context(headers, identity_headers))
    resolved_metadata: dict[str, Any] = {}
    if resolver:
        try:
            resolved = resolver(scope, headers) or {}
            fields.update(
                {
                    key: value
                    for key, value in resolved.items()
                    if key in _RESOLVER_FIELDS and key != "metadata"
                }
            )
            candidate_metadata = resolved.get("metadata")
            if isinstance(candidate_metadata, Mapping):
                resolved_metadata = dict(candidate_metadata)
        except Exception:
            logger.warning("watcher: HTTP context resolver failed", exc_info=True)

    request_id = headers.get("x-request-id")
    metadata: dict[str, Any] = {
        "http.method": str(scope.get("method", "")).upper(),
        "http.path": str(scope.get("path", "/")),
        "http.scheme": scope.get("scheme", ""),
    }
    if request_id:
        metadata["http.request_id"] = request_id
    metadata.update(resolved_metadata)
    fields["metadata"] = metadata
    fields.setdefault("channel", "http")
    return fields


def _input(scope: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "method": str(scope.get("method", "")).upper(),
        "path": str(scope.get("path", "/")),
    }


def _excluded(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def _trace_name(scope: Mapping[str, Any], resolver: NameResolver) -> str:
    try:
        return resolver(scope)
    except Exception:
        logger.warning("watcher: HTTP trace name resolver failed", exc_info=True)
        return _default_name(scope)


class WatcherASGIMiddleware:
    """Trace every HTTP request in any ASGI 3 application.

    ``identity_headers`` maps trusted context fields to request header names,
    for example ``{"user_id": "x-user-id", "session_id": "x-session-id"}``.
    A ``context_resolver`` can instead read authenticated state from ``scope``.
    """

    def __init__(
        self,
        app: Any,
        *,
        tracer: Tracer | None = None,
        identity_headers: Mapping[str, str] | None = None,
        context_resolver: ContextResolver | None = None,
        name_resolver: NameResolver | None = None,
        exclude_paths: Iterable[str] = _DEFAULT_EXCLUDED,
        response_trace_header: str | None = "x-watcher-trace-id",
    ) -> None:
        self.app = app
        self.tracer = tracer
        self.identity_headers = dict(identity_headers or {})
        self.context_resolver = context_resolver
        self.name_resolver = name_resolver or _default_name
        self.exclude_paths = tuple(exclude_paths)
        self.response_trace_header = (
            response_trace_header.lower() if response_trace_header else None
        )

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path", ""))
        if scope.get("type") != "http" or _excluded(path, self.exclude_paths):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        fields = _context_fields(
            scope, headers, self.identity_headers, self.context_resolver
        )
        tracer = self.tracer or get_tracer()
        status_code = 500

        name = _trace_name(scope, self.name_resolver)
        with tracer.trace(name, input=_input(scope), **fields) as context:
            async def send_with_trace(message: MutableMapping[str, Any]) -> None:
                nonlocal status_code
                if message.get("type") == "http.response.start":
                    status_code = int(message.get("status", 200))
                    if self.response_trace_header and context.trace_id:
                        response_headers = list(message.get("headers", []))
                        header_name = self.response_trace_header.encode("latin-1")
                        if not any(key.lower() == header_name for key, _ in response_headers):
                            response_headers.append(
                                (header_name, context.trace_id.encode("ascii"))
                            )
                            message = {**message, "headers": response_headers}
                await send(message)

            await self.app(scope, receive, send_with_trace)
            context.set_output({"status_code": status_code})


class WatcherWSGIMiddleware:
    """Trace every request in a WSGI application, including streamed bodies."""

    def __init__(
        self,
        app: Any,
        *,
        tracer: Tracer | None = None,
        identity_headers: Mapping[str, str] | None = None,
        context_resolver: ContextResolver | None = None,
        name_resolver: NameResolver | None = None,
        exclude_paths: Iterable[str] = _DEFAULT_EXCLUDED,
        response_trace_header: str | None = "x-watcher-trace-id",
    ) -> None:
        self.app = app
        self.tracer = tracer
        self.identity_headers = dict(identity_headers or {})
        self.context_resolver = context_resolver
        self.name_resolver = name_resolver or _default_name
        self.exclude_paths = tuple(exclude_paths)
        self.response_trace_header = response_trace_header

    def __call__(
        self, environ: MutableMapping[str, Any], start_response: Any
    ) -> Iterable[bytes]:
        path = str(environ.get("PATH_INFO", ""))
        if _excluded(path, self.exclude_paths):
            return self.app(environ, start_response)

        scope = {
            "type": "http",
            "method": environ.get("REQUEST_METHOD", ""),
            "path": path or "/",
            "scheme": environ.get("wsgi.url_scheme", ""),
            "environ": environ,
        }
        headers = {
            key[5:].replace("_", "-").lower(): str(value)
            for key, value in environ.items()
            if key.startswith("HTTP_")
        }
        fields = _context_fields(
            scope, headers, self.identity_headers, self.context_resolver
        )
        tracer = self.tracer or get_tracer()
        status_code = 500

        def traced_start_response(
            status: str, response_headers: list[tuple[str, str]], exc_info=None
        ):
            nonlocal status_code
            status_code = int(status.split(" ", 1)[0])
            if self.response_trace_header:
                trace_id = inject_trace_context().get("traceparent", "").split("-")
                if len(trace_id) > 1 and not any(
                    key.lower() == self.response_trace_header.lower()
                    for key, _ in response_headers
                ):
                    response_headers.append((self.response_trace_header, trace_id[1]))
            return start_response(status, response_headers, exc_info)

        def response() -> Iterable[bytes]:
            name = _trace_name(scope, self.name_resolver)
            with tracer.trace(name, input=_input(scope), **fields) as context:
                iterable = self.app(environ, traced_start_response)
                try:
                    yield from iterable
                    context.set_output({"status_code": status_code})
                finally:
                    close = getattr(iterable, "close", None)
                    if close:
                        close()

        return response()
