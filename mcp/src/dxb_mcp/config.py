"""Settings for the MCP server.

Same plain-dataclass style as the ELT and API configs (no pydantic-settings),
so all three services read env the same way and one habit covers the platform.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache

log = logging.getLogger(__name__)


def _get(name: str, default: str) -> str:
    # .strip() mirrors the other two loaders: some .env consumers keep trailing
    # whitespace that Docker Compose's parser would have trimmed.
    v = os.environ.get(name, "").strip()
    return v if v != "" else default


def _bool(name: str, default: str) -> bool:
    return _get(name, default) not in ("0", "false", "no", "off")


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in _get(name, default).split(",") if v.strip())


def _json_list(name: str) -> list[dict]:
    # Mirrors api/src/dxb_api/config.py's loader exactly, so DXB_API_KEYS and
    # DXB_MCP_CLIENT_API_KEYS are configured the same way and a key generated
    # for one format is trivially valid for the other.
    raw = _get(name, "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - config error path
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{name} must be a JSON array of objects")
    return parsed


@dataclass(frozen=True)
class Settings:
    # --- upstream REST API ---
    api_base_url: str
    api_key: str
    api_timeout_seconds: float

    # --- transport ---
    host: str
    port: int
    path: str

    # stdio is a debugging convenience and a second command channel on a public
    # host. Off unless explicitly switched on (docs/MCP_DESIGN.md §3).
    stdio_enabled: bool

    # DNS-rebinding protection. The SDK validates Host and Origin, which stops
    # a malicious web page from driving an MCP server reachable from the
    # victim's machine. Behind a reverse proxy the Host is whatever the client
    # sent, so the public hostnames have to be listed here or every proxied
    # request is rejected with "Invalid Host header".
    #
    # Kept ON with an explicit list rather than disabled — the check is cheap
    # and the attack it prevents is real.
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]

    # --- client -> MCP authentication (docs/MCP_DESIGN.md §4) ---
    #
    # A second, distinct credential from `api_key` above: `api_key` is what
    # THIS server presents to the REST API; these are what CALLERS of this
    # server must present to it. Same shape and hashing as the REST API's
    # `DXB_API_KEYS` ({"name", "key_hash", "scopes"}, SHA-256 hex) — not
    # imported from dxb_api (mcp/ owns no dependency on it, MCP_DESIGN.md §2),
    # but deliberately the same *format*, so one key-generation command
    # (README.md "Authentication") produces valid entries for either.
    client_api_keys: list[dict] = field(default_factory=list)
    # Local-dev escape hatch, mirroring DXB_AUTH_DISABLED on the REST API.
    # Off by default; never set in anything but a local .env.
    auth_disabled: bool = False

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return build_settings()


def build_settings() -> Settings:
    client_api_keys = _json_list("DXB_MCP_CLIENT_API_KEYS")
    auth_disabled = _bool("DXB_MCP_AUTH_DISABLED", "0")
    if auth_disabled:
        log.warning(
            "DXB_MCP_AUTH_DISABLED=1 — inbound authentication is OFF. Every "
            "tool is callable by anyone who can reach this port. This must "
            "never be set outside local development."
        )
    elif not client_api_keys:
        # Fails CLOSED, not open: with no keys configured, every request is
        # rejected — the safe direction to be wrong in, and the reason this is
        # a warning rather than a startup error the operator has to silence.
        log.warning(
            "DXB_MCP_CLIENT_API_KEYS is empty and auth is not disabled — "
            "every inbound request will be rejected with 401 until at least "
            "one key is configured. See README.md 'Authentication'."
        )
    return Settings(
        # Service DNS inside the compose network; nginx is in front of the
        # public edge, so this hop stays plain HTTP on the internal network.
        api_base_url=_get("DXB_MCP_API_URL", "http://api:8000").rstrip("/"),
        api_key=_get("DXB_MCP_API_KEY", ""),
        # Generous relative to a typical request: some analytics queries scan
        # years of marts, and an agent waiting is better than an agent
        # inventing an answer because the tool errored.
        api_timeout_seconds=float(_get("DXB_MCP_API_TIMEOUT", "30")),
        host=_get("DXB_MCP_HOST", "0.0.0.0"),
        port=int(_get("DXB_MCP_PORT", "8100")),
        path=_get("DXB_MCP_PATH", "/mcp"),
        stdio_enabled=_bool("DXB_MCP_STDIO", "0"),
        # Defaults cover local development through the nginx edge. A real
        # deployment must add its own hostname — see mcp/README.md.
        allowed_hosts=_csv(
            "DXB_MCP_ALLOWED_HOSTS", "localhost,localhost:443,localhost:8100,127.0.0.1"
        ),
        allowed_origins=_csv(
            "DXB_MCP_ALLOWED_ORIGINS", "https://localhost,http://localhost"
        ),
        client_api_keys=client_api_keys,
        auth_disabled=auth_disabled,
        log_level=_get("DXB_MCP_LOG_LEVEL", "INFO").upper(),
    )
