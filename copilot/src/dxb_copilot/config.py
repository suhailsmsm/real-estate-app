"""Settings for the copilot service.

Same plain-dataclass style as the ELT, API and MCP configs (no
pydantic-settings), so all four services read env the same way.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache

log = logging.getLogger(__name__)

_KNOWN_PROVIDERS = ("anthropic", "openai", "ollama")
# A hardcoded Anthropic model name is meaningless once another provider is
# selected — each provider gets its own sane default, still overridable by
# DXB_COPILOT_MODEL for any of them.
_DEFAULT_MODEL = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4.1",
    "ollama": "glm-5.2:cloud",
}
_CREDENTIAL_ENV_VAR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "DXB_COPILOT_OLLAMA_URL",
}


def _get(name: str, default: str) -> str:
    v = os.environ.get(name, "").strip()
    return v if v != "" else default


def _bool(name: str, default: str) -> bool:
    return _get(name, default) not in ("0", "false", "no", "off")


def _json_list(name: str) -> list[dict]:
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
    # --- upstream MCP server ---
    #
    # The copilot reaches data ONLY through MCP (docs/UI_PLAN.md §5): it has no
    # database credentials and no SQL of its own, by design. That is what keeps
    # "the API only reads the database, nothing else" true even once an LLM is
    # in the loop — the model can call exactly the seven curated tools the MCP
    # server exposes and nothing else.
    mcp_url: str
    mcp_api_key: str
    mcp_timeout_seconds: float

    # AUTH ONLY — never used to fetch data (that is exclusively MCP's job).
    # The SPA holds a JWT rather than an API key, and shipping an API key in a
    # browser bundle would publish it to every user; so a bearer token is
    # validated by presenting it to the API and seeing whether it is accepted,
    # rather than by giving this service a second copy of the signing secret.
    api_url: str

    # --- model provider ---
    #
    # One of "anthropic" | "openai" | "ollama" (providers/__init__.py's
    # get_provider() dispatches on this). Validated and normalized in
    # build_settings() — by the time it reaches anywhere else in this
    # service, it is guaranteed to be one of those three values.
    provider: str
    model: str
    max_tokens: int
    # A hard ceiling on tool-calling round trips. Without it a confused model
    # can loop on a failing tool until the request times out or the bill grows;
    # with it the user gets a partial answer and an explanation instead.
    max_turns: int

    # Only the field(s) relevant to the selected provider are ever read
    # (Settings.configured, providers/__init__.get_provider) — all three are
    # always present so switching DXB_COPILOT_PROVIDER needs no other change.
    anthropic_api_key: str
    openai_api_key: str
    ollama_url: str

    # --- transport ---
    host: str
    port: int

    # Set to "/copilot" in compose, empty everywhere else — same purpose and
    # same value shape as api/'s DXB_API_ROOT_PATH. nginx strips the prefix
    # before forwarding, so this app only ever *receives* unprefixed paths;
    # root_path changes nothing about routing. It exists so FastAPI's
    # generated URLs (the /docs page's embedded openapi.json fetch) point back
    # through the proxy instead of at the container's own unprefixed root.
    # Without it, /copilot/docs through nginx renders Swagger UI whose
    # openapi.json fetch goes to site-root "/" — which nginx now routes to the
    # SPA container — so Swagger receives index.html and reports "does not
    # specify a valid version field". Caught for real, not by inspection.
    root_path: str = ""

    # --- client -> copilot authentication ---
    #
    # Same shape and hashing as DXB_API_KEYS / DXB_MCP_CLIENT_API_KEYS
    # ({"name", "key_hash", "scopes"}, SHA-256 hex), so one key-generation
    # command works for all three services.
    client_api_keys: list[dict] = field(default_factory=list)
    auth_disabled: bool = False

    log_level: str = "INFO"

    @property
    def configured(self) -> bool:
        """False when the service cannot possibly answer with the selected provider.

        Checked at request time rather than refused at startup so the container
        still boots, still serves /health, and returns one clear explanation to
        the user instead of a crash loop that looks like a broken deployment.

        Anthropic and OpenAI need their vendor API key; Ollama has no key
        concept at all — reachability, not credentials, is what "configured"
        means there, and ollama_url always has a default, so this is
        effectively always true for that provider (a genuinely unreachable
        host still surfaces as a clear per-request error, just later, at the
        first /chat call rather than here).
        """
        if self.provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.provider == "openai":
            return bool(self.openai_api_key)
        if self.provider == "ollama":
            return bool(self.ollama_url)
        return False  # unreachable — provider is validated in build_settings()

    @property
    def missing_credential_hint(self) -> str:
        """The one env var to set for the selected provider, for the
        not-configured error message (agent.py)."""
        return _CREDENTIAL_ENV_VAR.get(self.provider, "DXB_COPILOT_PROVIDER")


@lru_cache
def get_settings() -> Settings:
    return build_settings()


def build_settings() -> Settings:
    client_api_keys = _json_list("DXB_COPILOT_CLIENT_API_KEYS")
    auth_disabled = _bool("DXB_COPILOT_AUTH_DISABLED", "0")
    if auth_disabled:
        log.warning(
            "DXB_COPILOT_AUTH_DISABLED=1 — inbound authentication is OFF. "
            "Anyone who can reach this port can spend model tokens. This must "
            "never be set outside local development."
        )
    elif not client_api_keys:
        # Fails CLOSED, same as the MCP server: with no keys configured every
        # request is rejected, which is the safe direction to be wrong in.
        log.warning(
            "DXB_COPILOT_CLIENT_API_KEYS is empty and auth is not disabled — "
            "every inbound request will be rejected with 401 until at least "
            "one key is configured."
        )

    # Validated and normalized HERE, once — build_settings() runs at
    # container import time (server.py's module-level `app = create_app()`),
    # so anything downstream (get_provider(), the _DEFAULT_MODEL lookup two
    # lines down) can assume `provider` is always one of the three known
    # values without risking a KeyError crash-looping the container over a
    # typo'd env var. Same fail-soft philosophy as the missing-credential
    # warning below: log it, fall back, keep booting.
    provider = _get("DXB_COPILOT_PROVIDER", "anthropic").lower()
    if provider not in _KNOWN_PROVIDERS:
        log.warning(
            "DXB_COPILOT_PROVIDER=%r is not one of %s — falling back to 'anthropic'.",
            provider,
            _KNOWN_PROVIDERS,
        )
        provider = "anthropic"

    anthropic_api_key = _get("ANTHROPIC_API_KEY", "")
    openai_api_key = _get("OPENAI_API_KEY", "")
    ollama_url = _get("DXB_COPILOT_OLLAMA_URL", "http://host.docker.internal:11434")

    selected_credential = {
        "anthropic": anthropic_api_key,
        "openai": openai_api_key,
        "ollama": ollama_url,
    }[provider]
    if not selected_credential:
        log.warning(
            "%s is not set for the selected provider (%s) — the copilot will "
            "boot and serve /health, but every chat request will return a "
            "configuration error explaining what is missing. See "
            "copilot/README.md.",
            _CREDENTIAL_ENV_VAR[provider],
            provider,
        )

    return Settings(
        mcp_url=_get("DXB_COPILOT_MCP_URL", "http://mcp:8100/mcp"),
        api_url=_get("DXB_COPILOT_API_URL", "http://api:8000").rstrip("/"),
        mcp_api_key=_get("DXB_MCP_CLIENT_API_KEY", ""),
        mcp_timeout_seconds=float(_get("DXB_COPILOT_MCP_TIMEOUT", "60")),
        provider=provider,
        model=_get("DXB_COPILOT_MODEL", _DEFAULT_MODEL.get(provider, "")),
        max_tokens=int(_get("DXB_COPILOT_MAX_TOKENS", "4096")),
        max_turns=int(_get("DXB_COPILOT_MAX_TURNS", "8")),
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
        ollama_url=ollama_url,
        host=_get("DXB_COPILOT_HOST", "0.0.0.0"),
        port=int(_get("DXB_COPILOT_PORT", "8200")),
        root_path=_get("DXB_COPILOT_ROOT_PATH", ""),
        client_api_keys=client_api_keys,
        auth_disabled=auth_disabled,
        log_level=_get("DXB_COPILOT_LOG_LEVEL", "INFO").upper(),
    )
