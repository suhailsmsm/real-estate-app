"""Settings for the read-only API.

Same plain-dataclass style as the ELT's config (no pydantic-settings) so both
services read env the same way. Everything is resolved once at startup.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache

log = logging.getLogger(__name__)


def _get(name: str, default: str) -> str:
    # .strip() mirrors the ELT's loader: some .env consumers keep trailing
    # whitespace that Docker Compose's parser would have trimmed.
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
    # --- database (read replica ready: only the host changes) ---
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    db_pool_size: int
    db_max_overflow: int
    db_statement_timeout_ms: int

    # --- auth ---
    auth_disabled: bool
    jwt_private_key: str
    jwt_public_key: str
    jwt_kid: str
    jwt_issuer: str
    jwt_audience: str
    access_ttl_seconds: int
    refresh_ttl_seconds: int
    users: list[dict] = field(default_factory=list)
    api_keys: list[dict] = field(default_factory=list)

    # --- query bounds (LLM-safety, API_DESIGN.md §6) ---
    max_page_limit: int = 1000
    default_page_limit: int = 100
    max_entity_ids: int = 200
    default_min_sample: int = 20
    trgm_threshold: float = 0.30
    # Two candidates whose similarity differs by less than this are treated as
    # ambiguous -> 422 with candidates, rather than silently picking one.
    trgm_ambiguity_margin: float = 0.05

    # Set to "/api" in compose (docker-compose.yml), empty everywhere else.
    # nginx strips the /api prefix before forwarding (nginx.conf's `rewrite`),
    # so this app only ever *receives* unprefixed paths — root_path changes
    # nothing about routing. It exists purely so FastAPI's generated URLs
    # (the docs page's embedded openapi.json fetch, the openapi spec's
    # `servers` entry) point back through the proxy instead of at the
    # container's own unprefixed root, which 404s from outside nginx. See
    # https://fastapi.tiangolo.com/advanced/behind-a-proxy/.
    root_path: str = ""

    @property
    def dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


def _load_keys() -> tuple[str, str]:
    """Return (private_pem, public_pem).

    Keys come from env, or from a file path for real deployments. If neither is
    set we generate an ephemeral pair so `docker compose up` works out of the
    box — tokens then die with the process, which is loudly warned about and is
    the correct behaviour for a throwaway key.
    """
    priv = _get("DXB_JWT_PRIVATE_KEY", "")
    pub = _get("DXB_JWT_PUBLIC_KEY", "")
    priv_file = _get("DXB_JWT_PRIVATE_KEY_FILE", "")
    if not priv and priv_file:
        with open(priv_file, encoding="utf-8") as fh:
            priv = fh.read()
    if priv:
        if not pub:
            pub = _derive_public_key(priv)
        return priv, pub

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    log.warning(
        "No DXB_JWT_PRIVATE_KEY configured — generating an EPHEMERAL RSA key. "
        "All issued tokens become invalid when this process restarts. "
        "Set DXB_JWT_PRIVATE_KEY(_FILE) for anything but local development."
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return priv, pub


def _derive_public_key(private_pem: str) -> str:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


def build_settings() -> Settings:
    auth_disabled = _bool("DXB_AUTH_DISABLED", "0")
    if auth_disabled:
        log.warning(
            "DXB_AUTH_DISABLED=1 — authentication is OFF. Every endpoint is "
            "open. This must never be set outside local development."
        )
    priv, pub = ("", "") if auth_disabled else _load_keys()
    return Settings(
        db_host=_get("DXB_DB_HOST", "localhost"),
        db_port=int(_get("DXB_DB_PORT", "5432")),
        # Defaults to the read-only role, not the ELT's read-write user
        # (API_DESIGN.md §4, layer 1).
        db_user=_get("DXB_API_DB_USER", "dxb_readonly"),
        db_password=_get("DXB_API_DB_PASSWORD", ""),
        db_name=_get("POSTGRES_DB", "dxb"),
        db_pool_size=int(_get("DXB_API_DB_POOL_SIZE", "10")),
        db_max_overflow=int(_get("DXB_API_DB_MAX_OVERFLOW", "5")),
        db_statement_timeout_ms=int(_get("DXB_API_STATEMENT_TIMEOUT_MS", "15000")),
        auth_disabled=auth_disabled,
        jwt_private_key=priv,
        jwt_public_key=pub,
        jwt_kid=_get("DXB_JWT_KID", "dxb-api-1"),
        jwt_issuer=_get("DXB_JWT_ISSUER", "dxb-api"),
        jwt_audience=_get("DXB_JWT_AUDIENCE", "dxb-clients"),
        access_ttl_seconds=int(_get("DXB_ACCESS_TTL_SECONDS", "900")),
        refresh_ttl_seconds=int(_get("DXB_REFRESH_TTL_SECONDS", "604800")),
        users=_json_list("DXB_API_USERS"),
        api_keys=_json_list("DXB_API_KEYS"),
        max_page_limit=int(_get("DXB_MAX_PAGE_LIMIT", "1000")),
        default_page_limit=int(_get("DXB_DEFAULT_PAGE_LIMIT", "100")),
        max_entity_ids=int(_get("DXB_MAX_ENTITY_IDS", "200")),
        default_min_sample=int(_get("DXB_DEFAULT_MIN_SAMPLE", "20")),
        trgm_threshold=float(_get("DXB_TRGM_THRESHOLD", "0.30")),
        trgm_ambiguity_margin=float(_get("DXB_TRGM_AMBIGUITY_MARGIN", "0.05")),
        root_path=_get("DXB_API_ROOT_PATH", ""),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return build_settings()
