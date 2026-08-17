"""Inbound authentication.

Same shape and hashing as the REST API's DXB_API_KEYS and the MCP server's
DXB_MCP_CLIENT_API_KEYS ({"name", "key_hash", "scopes"}, SHA-256 hex), so one
key-generation command produces a valid entry for any of the three services.

Fails CLOSED: with no keys configured every request is rejected. This service
spends money per request, so "wrong in the direction of refusing" is the only
acceptable direction to be wrong in.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import httpx

from .config import Settings

log = logging.getLogger(__name__)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def verify_bearer(settings: Settings, token: str) -> bool:
    """Validate a browser's access token by asking the API to accept it.

    The SPA holds a JWT, not an API key — and shipping an API key in a browser
    bundle would publish it to every user, so that is not an option. Rather
    than sharing the API's signing secret with this service (a second copy of a
    credential is a second place to leak it), we simply present the token to an
    endpoint that requires auth and see whether it is accepted.

    This is the ONLY reason this service knows the API's address. It never
    fetches data that way — data comes exclusively through MCP
    (docs/UI_PLAN.md §5). The endpoint used is deliberately a tiny metadata
    read, so the check costs a round trip and nothing else.
    """
    if not settings.api_url:
        log.warning("Bearer presented but DXB_COPILOT_API_URL is unset — rejecting")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                f"{settings.api_url}/meta/coverage",
                headers={"Authorization": f"Bearer {token}"},
            )
        return res.status_code == 200
    except httpx.HTTPError as exc:
        # Fail closed: an unreachable API means we cannot establish who this
        # is, which is not the same as establishing that they are allowed.
        log.warning("Bearer verification failed: %s", exc)
        return False


def authenticate(settings: Settings, presented: str | None) -> bool:
    if settings.auth_disabled:
        return True
    if not presented:
        return False
    digest = hash_key(presented)
    # compare_digest per candidate rather than a set lookup: constant-time
    # comparison keeps the check from leaking key material through timing.
    return any(
        hmac.compare_digest(digest, str(entry.get("key_hash", "")))
        for entry in settings.client_api_keys
    )
