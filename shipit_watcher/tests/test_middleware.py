"""Framework-neutral HTTP instrumentation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from shipit_watcher.context import TraceContext, get_trace_id
from shipit_watcher.middleware import WatcherASGIMiddleware, WatcherWSGIMiddleware
from shipit_watcher.tracer import Tracer


class RecordingSink:
    def __init__(self):
        self.started: list[tuple[str, str, TraceContext, Any]] = []
        self.ended: list[tuple[str, Any, Any]] = []

    def start_trace(self, trace_id, name, context, input_data=None):
        self.started.append((trace_id, name, context, input_data))

    def end_trace(self, trace_id, output=None, metadata=None):
        self.ended.append((trace_id, output, metadata))

    def record(self, event, context):
        pass

    def flush(self):
        pass


def _asgi_scope(path: str = "/chat") -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "scheme": "https",
        "headers": [
            (b"traceparent", b"00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"),
            (b"x-user-id", b"user-7"),
            (b"x-session-id", b"session-9"),
            (b"x-request-id", b"request-3"),
        ],
    }


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _ignore_send(message):
    return None


class TestASGIMiddleware:
    def test_records_request_context_response_and_propagation(self):
        sink = RecordingSink()
        tracer = Tracer(sinks=[sink])
        sent = []
        active_ids = []

        async def app(scope, receive, send):
            active_ids.append(get_trace_id())
            await send({"type": "http.response.start", "status": 201, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = WatcherASGIMiddleware(
            app,
            tracer=tracer,
            identity_headers={"user_id": "x-user-id", "session_id": "x-session-id"},
        )

        async def capture_send(message):
            sent.append(message)

        async def run():
            await middleware(_asgi_scope(), _receive, capture_send)

        asyncio.run(run())

        trace_id, name, context, input_data = sink.started[0]
        assert trace_id == "a" * 32
        assert name == "http.post /chat"
        assert context.remote_parent_id == "b" * 16
        assert context.user_id == "user-7"
        assert context.session_id == "session-9"
        assert context.metadata["http.request_id"] == "request-3"
        assert context.metadata["http.method"] == "POST"
        assert input_data == {"method": "POST", "path": "/chat"}
        assert active_ids == [trace_id]
        assert sink.ended[0][1] == {"status_code": 201}
        assert (b"x-watcher-trace-id", trace_id.encode()) in sent[0]["headers"]

    def test_stream_stays_inside_trace_until_completion(self):
        sink = RecordingSink()
        tracer = Tracer(sinks=[sink])
        active_ids = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            active_ids.append(get_trace_id())
            await asyncio.sleep(0)
            active_ids.append(get_trace_id())
            await send({"type": "http.response.body", "body": b"done"})

        middleware = WatcherASGIMiddleware(app, tracer=tracer)
        asyncio.run(middleware(_asgi_scope(), _receive, _ignore_send))
        assert active_ids == ["a" * 32, "a" * 32]

    def test_excluded_and_non_http_scopes_pass_through(self):
        sink = RecordingSink()
        calls = []

        async def app(scope, receive, send):
            calls.append(scope["type"])

        middleware = WatcherASGIMiddleware(app, tracer=Tracer(sinks=[sink]))
        asyncio.run(middleware(_asgi_scope("/health"), _receive, _ignore_send))
        websocket = {**_asgi_scope(), "type": "websocket"}
        asyncio.run(middleware(websocket, _receive, _ignore_send))
        assert calls == ["http", "websocket"]
        assert sink.started == []

    def test_application_errors_are_recorded_and_reraised(self):
        sink = RecordingSink()

        async def app(scope, receive, send):
            raise LookupError("failed")

        middleware = WatcherASGIMiddleware(app, tracer=Tracer(sinks=[sink]))
        with pytest.raises(LookupError, match="failed"):
            asyncio.run(middleware(_asgi_scope(), _receive, _ignore_send))
        assert sink.ended[0][1] == {"error": "LookupError: failed"}

    def test_broken_context_resolver_never_breaks_the_request(self):
        sink = RecordingSink()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 204})

        def resolver(scope, headers):
            raise RuntimeError("identity service unavailable")

        middleware = WatcherASGIMiddleware(
            app, tracer=Tracer(sinks=[sink]), context_resolver=resolver
        )
        asyncio.run(middleware(_asgi_scope(), _receive, _ignore_send))
        assert sink.ended[0][1] == {"status_code": 204}

    def test_resolver_attaches_authenticated_dimensions_tags_and_metadata(self):
        sink = RecordingSink()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})

        def resolver(scope, headers):
            return {
                "company_id": scope["state"]["company_id"],
                "cost_center": "support",
                "tags": ["tier:enterprise"],
                "metadata": {"region": "eu"},
            }

        scope = {**_asgi_scope(), "state": {"company_id": "acme"}}
        middleware = WatcherASGIMiddleware(
            app, tracer=Tracer(sinks=[sink]), context_resolver=resolver
        )
        asyncio.run(middleware(scope, _receive, _ignore_send))
        context = sink.started[0][2]
        assert context.company_id == "acme"
        assert context.cost_center == "support"
        assert context.tags == ["tier:enterprise"]
        assert context.metadata["region"] == "eu"

    def test_default_name_normalises_high_cardinality_path_ids(self):
        sink = RecordingSink()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})

        scope = _asgi_scope("/users/123/runs/550e8400-e29b-41d4-a716-446655440000")
        middleware = WatcherASGIMiddleware(app, tracer=Tracer(sinks=[sink]))
        asyncio.run(middleware(scope, _receive, _ignore_send))
        assert sink.started[0][1] == "http.post /users/{id}/runs/{id}"
        assert sink.started[0][3]["path"].endswith("446655440000")


class TestWSGIMiddleware:
    def test_streaming_wsgi_response_is_fully_traced(self):
        sink = RecordingSink()
        seen_ids = []
        response = {}

        def app(environ, start_response):
            start_response("202 Accepted", [("Content-Type", "text/plain")])

            def body():
                seen_ids.append(get_trace_id())
                yield b"one"
                seen_ids.append(get_trace_id())
                yield b"two"

            return body()

        def start_response(status, headers, exc_info=None):
            response.update(status=status, headers=headers)

        middleware = WatcherWSGIMiddleware(app, tracer=Tracer(sinks=[sink]))
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/events",
            "wsgi.url_scheme": "https",
        }
        assert b"".join(middleware(environ, start_response)) == b"onetwo"
        trace_id = sink.started[0][0]
        assert seen_ids == [trace_id, trace_id]
        assert sink.ended[0][1] == {"status_code": 202}
        assert ("x-watcher-trace-id", trace_id) in response["headers"]
