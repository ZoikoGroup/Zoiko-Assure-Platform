"""Request-body size limits for upload endpoints, enforced while the body streams in.

Multipart parsing spools a whole upload (to memory, then disk) before the endpoint runs, so a limit checked inside the
endpoint comes too late. This ASGI middleware counts the bytes it hands on and refuses the request with 413 as soon as
the limit is passed, whether Content-Length is honest, wrong or absent, so at most `limit` bytes are ever read.
A reverse proxy in front of the API should enforce the same or a lower limit.
"""
from __future__ import annotations

import json
from collections.abc import Callable


class _TooLarge(Exception):
    pass


class BodyLimit:
    """`paths` maps an exact request path to a function returning its byte limit (read per request, so it follows
    the environment)."""

    def __init__(self, app, paths: dict[str, Callable[[], int]]):
        self.app, self.paths = app, paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in self.paths:
            await self.app(scope, receive, send)
            return
        limit = self.paths[scope["path"]]()
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await _reject(send, limit)  # refuse before reading any of the body
            return

        state = {"received": 0, "exceeded": False, "started": False}

        async def counted_receive():
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > limit:
                    state["exceeded"] = True
                    raise _TooLarge()
            return message

        async def guarded_send(message):
            # The framework may turn the interrupted body into its own error response; answer 413 instead.
            if state["exceeded"]:
                if message["type"] == "http.response.start" and not state["started"]:
                    state["started"] = True
                    await _reject(send, limit)
                return
            if message["type"] == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except _TooLarge:
            if not state["started"]:
                await _reject(send, limit)


async def _reject(send, limit: int) -> None:
    body = json.dumps({"detail": f"Upload exceeds the {limit} byte limit"}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
