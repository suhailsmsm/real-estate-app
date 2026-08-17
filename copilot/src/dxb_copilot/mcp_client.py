"""Thin MCP client — the copilot's only route to data.

This service holds no database credentials and writes no SQL. Everything it
knows about Dubai real estate arrives through the seven curated tools the MCP
server exposes, which are themselves a thin layer over the read-only REST API
(docs/MCP_DESIGN.md §2). Three properties fall out of that, and all three are
the reason it is built this way:

  * The model cannot reach anything the MCP server does not offer.
  * Every answer inherits the API's own guardrails — sample sizes, caveats,
    the refusal to scan facts unfiltered, the ambiguous-area error.
  * Adding a capability is a deliberate act in one place, not an emergent
    property of a prompt.

The MCP SDK is pinned exactly (see copilot/pyproject.toml, mirroring mcp/'s
reasoning): v2 tracks a spec that is still a release candidate, so an SDK bump
must be a deliberate, tested change.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from .providers.base import ToolSchema

log = logging.getLogger(__name__)


@asynccontextmanager
async def mcp_session(url: str, api_key: str, timeout_seconds: float):
    """Open one MCP session for the lifetime of a single chat request.

    Deliberately per-request rather than a long-lived shared session: sessions
    carry server-side state, and sharing one across concurrent users would let
    their tool traffic interleave. Connection setup is cheap next to a model
    round trip, so the simpler, safer shape wins.

    Auth rides on the httpx client's default headers rather than being passed
    to the transport: in SDK v2 `StreamableHTTPTransport` builds its own
    protocol headers and takes no custom ones, so the supported injection point
    is the HTTP client it is handed.
    """
    http_client = create_mcp_http_client(
        headers={"X-API-Key": api_key} if api_key else None,
        timeout=httpx2.Timeout(timeout_seconds),
    )
    async with http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def to_tool_schemas(mcp_tools: list[Any]) -> list[ToolSchema]:
    """Convert MCP tool descriptors into the canonical `ToolSchema` shape
    every provider understands (providers/base.py).

    Provider-neutral by construction, not just in name: JSON Schema is what
    MCP already gives us (`inputSchema`) and what every provider's own
    tool-definition format wraps, so this is a rename/reshape rather than a
    translation. Descriptions are passed through untouched: they are written
    for a model and are the main thing steering correct tool choice.
    """
    out: list[ToolSchema] = []
    for t in mcp_tools:
        schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
        out.append(
            ToolSchema(
                name=t.name,
                description=(getattr(t, "description", "") or "").strip(),
                schema=schema,
            )
        )
    return out


def flatten_tool_result(result: Any) -> str:
    """Reduce an MCP tool result to text for the model.

    Kept as text rather than re-parsed into structures on purpose: the MCP
    server already formats these for model consumption, and re-shaping them
    here would be a second place to get the presentation wrong.
    """
    if getattr(result, "isError", False):
        return f"TOOL ERROR: {_content_text(result)}"
    return _content_text(result)


def _content_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    if not parts:
        # Some tools answer with structured content only.
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return str(structured)
        return "(tool returned no content)"
    return "\n".join(parts)
