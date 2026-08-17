"""Enforces the client -> MCP `X-API-Key` (docs/MCP_DESIGN.md §4).

A raw ASGI middleware, not Starlette's `BaseHTTPMiddleware`. `BaseHTTPMiddleware`
runs the downstream app to completion and buffers its entire response before
your code sees it, which is exactly wrong for a Streamable HTTP endpoint whose
whole point is that responses may stream as SSE — buffering would silently
turn streaming back into "wait for everything, then reply", the same class of
bug §8's nginx `proxy_buffering off` note exists to prevent, just one layer up
the stack. This middleware never touches the response at all: it inspects only
the request headers and either forwards the call unchanged or answers directly
with a 401, before the wrapped app ever runs.
"""

from __future__ import annotations

import json
import logging

from dxb_mcp.auth import verify_api_key
from dxb_mcp.config import Settings

log = logging.getLogger(__name__)

# Unauthenticated on purpose, matching the REST API's /health (main.py):
# it's the compose healthcheck, hit constantly and from inside the network
# only, and a DB- or auth-gated liveness probe is a footgun, not a feature.
_EXEMPT_PATHS = frozenset({"/health"})


class ApiKeyAuthMiddleware:
    def __init__(self, app, settings: Settings):
        self._app = app
        self._settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in _EXEMPT_PATHS:
            await self._app(scope, receive, send)
            return

        if self._settings.auth_disabled:
            await self._app(scope, receive, send)
            return

        principal = verify_api_key(self._settings.client_api_keys, _header(scope))
        if principal is None:
            await _unauthorized(send)
            return

        # Logged, not otherwise used yet: per-consumer attribution for the MCP
        # server's own traffic, since the REST API sees every MCP call as the
        # single `mcp` service principal (MCP_DESIGN.md §4's accepted tradeoff).
        log.info("authenticated MCP request from %s", principal.subject)
        await self._app(scope, receive, send)


def _header(scope) -> str:
    for name, value in scope.get("headers", ()):
        if name.lower() == b"x-api-key":
            return value.decode("latin-1")
    return ""


async def _unauthorized(send) -> None:
    body = json.dumps(
        {
            "error": "unauthorized",
            "message": "Missing or invalid X-API-Key.",
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"ApiKey"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
