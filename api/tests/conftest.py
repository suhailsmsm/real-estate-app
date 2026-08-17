"""Shared fixtures for the API suite.

Unit tests only: no real network, no real database. Repositories are replaced
via FastAPI's `dependency_overrides`, which is exactly the seam the layering
in API_DESIGN.md §3 was designed to give us — routers already receive
repository *instances*, so a fake slots straight in.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from dxb_api.config import Settings, build_settings

# Same rationale as the ELT's conftest: Settings is built eagerly and in full,
# so a test touching one corner still reads every env var. Without clearing
# them, a developer's sourced .env leaks in and breaks unrelated tests
# non-reproducibly.
_ENV_VARS = [
    "DXB_DB_HOST",
    "DXB_DB_PORT",
    "POSTGRES_DB",
    "DXB_API_DB_USER",
    "DXB_API_DB_PASSWORD",
    "DXB_API_DB_POOL_SIZE",
    "DXB_API_DB_MAX_OVERFLOW",
    "DXB_API_STATEMENT_TIMEOUT_MS",
    "DXB_AUTH_DISABLED",
    "DXB_JWT_PRIVATE_KEY",
    "DXB_JWT_PUBLIC_KEY",
    "DXB_JWT_PRIVATE_KEY_FILE",
    "DXB_JWT_KID",
    "DXB_JWT_ISSUER",
    "DXB_JWT_AUDIENCE",
    "DXB_ACCESS_TTL_SECONDS",
    "DXB_REFRESH_TTL_SECONDS",
    "DXB_API_USERS",
    "DXB_API_KEYS",
    "DXB_MAX_PAGE_LIMIT",
    "DXB_DEFAULT_PAGE_LIMIT",
    "DXB_MAX_ENTITY_IDS",
    "DXB_DEFAULT_MIN_SAMPLE",
    "DXB_TRGM_THRESHOLD",
    "DXB_TRGM_AMBIGUITY_MARGIN",
    "DXB_API_ROOT_PATH",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def rsa_keypair() -> tuple[str, str]:
    """One 2048-bit keypair for the whole session — generating it per test
    costs ~0.5s each and buys nothing."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

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


@pytest.fixture
def settings(rsa_keypair, monkeypatch) -> Settings:
    priv, pub = rsa_keypair
    monkeypatch.setenv("DXB_JWT_PRIVATE_KEY", priv)
    monkeypatch.setenv("DXB_JWT_PUBLIC_KEY", pub)
    return build_settings()


@pytest.fixture
def open_settings(monkeypatch) -> Settings:
    """Auth disabled — for exercising routers without minting tokens."""
    monkeypatch.setenv("DXB_AUTH_DISABLED", "1")
    return build_settings()


@pytest.fixture
def client(open_settings):
    """App with a stub sessionmaker.

    Every test that reaches a repository overrides it, so the sessionmaker is
    never actually used; it exists so the lifespan does not try to connect.
    """
    from dxb_api.main import create_app

    app = create_app(open_settings)
    app.router.lifespan_context = _no_db_lifespan
    with TestClient(app) as c:
        yield c


@pytest.fixture
def proxied_client(monkeypatch, open_settings):
    """The same app, but as nginx actually serves it: mounted at /api.

    Exists because of a real bug: every endpoint tested green under `client`
    (root-mounted), but the rendered docs page 404'd in a browser through
    nginx, because FastAPI had no idea it was reachable under a prefix and
    embedded an unprefixed `/openapi.json` link. Curling each URL directly
    never caught it — only checking what the docs page itself *fetches* does.
    """
    monkeypatch.setenv("DXB_API_ROOT_PATH", "/api")
    from dxb_api.config import build_settings
    from dxb_api.main import create_app

    app = create_app(build_settings())  # auth_disabled still set by open_settings
    app.router.lifespan_context = _no_db_lifespan
    with TestClient(app) as c:
        yield c


@asynccontextmanager
async def _no_db_lifespan(app):
    """Replaces the real lifespan so no engine is created and nothing
    connects. Tests that reach a repository override it instead."""
    app.state.sessionmaker = None
    yield


@pytest.fixture
def override(client):
    """Swap a repository dependency for a fake."""

    def _override(dep_annotated, fake):
        # Annotated[X, Depends(f)] -> the f that FastAPI keys overrides on.
        marker = dep_annotated.__metadata__[0]
        client.app.dependency_overrides[marker.dependency] = lambda: fake

    yield _override
    client.app.dependency_overrides.clear()


os.environ.setdefault("PYTHONHASHSEED", "0")
