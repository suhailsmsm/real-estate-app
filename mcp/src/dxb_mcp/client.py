"""HTTP client for the REST API, plus the error translation that goes with it.

The MCP server answers nothing itself (docs/MCP_DESIGN.md §2): every tool call
becomes one or more calls through here. That is deliberate — the REST API
already enforces read-only access, auth, bounded queries, fuzzy entity
resolution and the caveat blocks, and a second implementation of any of that
would be a second place for it to drift.

Statelessness (§3) shapes this module: the client holds a connection pool and
nothing else. No per-conversation caches, no memoized entity lookups keyed by
anything an agent said earlier. A pool is shared infrastructure; remembered
answers would be state, and state is what we declined.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from dxb_mcp.config import Settings

log = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=settings.api_timeout_seconds,
            headers=_auth_headers(settings),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET, with REST errors translated into MCP tool errors.

        Everything this service does is a read, so one verb is the whole
        surface. If a second one is ever needed here, that is a sign the
        design in §2 has been breached.
        """
        try:
            response = await self._client.get(path, params=_clean(params))
        except httpx.TimeoutException as exc:
            raise ToolError(
                f"The analytics API did not respond within "
                f"{self._settings.api_timeout_seconds:.0f}s. The query may be "
                f"too broad — try a narrower date range or fewer entities."
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not reach the analytics API: {exc}") from exc

        if response.is_success:
            return response.json()
        raise _translate(response)


def _auth_headers(settings: Settings) -> dict[str, str]:
    # Missing key is not fatal at construction: the API may be running with
    # auth disabled in local development, and failing to start would be a
    # worse experience than a clear 401 on the first call.
    return {"X-API-Key": settings.api_key} if settings.api_key else {}


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    """Drop unset optionals so the API applies its own defaults.

    Passing `None` through as a literal would override a default with an empty
    value on some query parsers — a silent behaviour change rather than an
    error.
    """
    if not params:
        return {}
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        out[key] = ",".join(str(v) for v in value) if isinstance(value, list) else value
    return out


def _translate(response: httpx.Response) -> ToolError:
    """Turn a REST error into a tool error an agent can act on.

    The one that matters is 422-with-candidates. The API refuses to guess which
    "Marina" you meant and returns the candidates instead; if that became a
    generic failure here, the agent would retry the same ambiguous string or
    silently pick one. Forwarding the candidates turns a dead end into a
    disambiguation question — the honesty rule in §7 that is easiest to lose in
    a wrapper.
    """
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    kind = payload.get("error", "")
    message = payload.get("message") or response.reason_phrase

    candidates = payload.get("candidates")
    if candidates:
        listed = "; ".join(
            f"{c.get('name_en', '?')} (id={c.get('id')}, "
            f"similarity={c.get('similarity')})"
            for c in candidates[:10]
        )
        return ToolError(
            f"{message} This name is ambiguous — it was NOT resolved to any "
            f"single entity. Candidates: {listed}. Ask the user which one they "
            f"mean, or call again with the chosen id."
        )

    if response.status_code == 401:
        return ToolError(
            "The analytics API rejected our credentials. This is a server "
            "configuration problem, not something the request can fix."
        )
    if response.status_code == 404:
        return ToolError(f"Not found: {message}")
    if response.status_code == 422:
        required = payload.get("required_one_of")
        hint = f" Provide at least one of: {', '.join(required)}." if required else ""
        return ToolError(f"Invalid request: {message}{hint}")
    if response.status_code == 429:
        return ToolError("Rate limited by the analytics API. Slow down and retry.")
    if response.status_code >= 500:
        log.error("upstream %s on %s", response.status_code, response.request.url)
        return ToolError(
            "The analytics API failed to process this request. This is not a "
            "problem with the question — do not rephrase and retry immediately."
        )
    return ToolError(f"{kind or 'Request failed'}: {message}")
