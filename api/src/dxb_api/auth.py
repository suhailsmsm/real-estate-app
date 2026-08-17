"""Self-issued RS256 JWTs, argon2id passwords, and static API keys.

Design notes that are easy to get wrong later (API_DESIGN.md §5):

* **RS256, not HS256.** The MCP server and any future consumer can verify
  tokens holding only the public key. `_ALGORITHMS` is an explicit allow-list
  so a forged `alg: none` or an HS256 token signed with the *public* key is
  rejected outright.
* **No users table.** Users and API keys live in configuration and refresh
  tokens are stateless, so the service performs no writes of any kind — which
  is what keeps the read-only guarantee in §4 intact.
* **Passwords use argon2id, API keys use SHA-256.** That asymmetry is
  deliberate, not an oversight: argon2 is memory-hard *by design* (~50-100 ms)
  to resist brute force against human-chosen passwords. An API key is a
  high-entropy random secret with no dictionary to attack, so a fast digest is
  cryptographically sufficient — and it must be fast, because it is verified
  on *every single request* rather than once per login.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio.to_thread
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from dxb_api.config import Settings

log = logging.getLogger(__name__)

_ALGORITHMS = ["RS256"]
_hasher = PasswordHasher()  # argon2id defaults


class AuthError(Exception):
    """Credentials or token rejected. Rendered as 401 by main.py."""


@dataclass(frozen=True)
class Principal:
    """Who is calling. Identical shape for token and API-key callers."""

    subject: str
    kind: str  # "user" | "api_key" | "auth-disabled"
    scopes: tuple[str, ...] = ("read",)


# ------------------------------------------------------------------ hashing


def hash_password(password: str) -> str:
    """Produce an argon2id hash — for generating config values, not runtime."""
    return _hasher.hash(password)


def _verify_password_blocking(stored_hash: str, password: str) -> bool:
    try:
        _hasher.verify(stored_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception:  # malformed hash in config
        log.exception("argon2 verification failed on a malformed stored hash")
        return False


async def verify_password(stored_hash: str, password: str) -> bool:
    """Verify off the event loop.

    argon2 is intentionally slow and CPU-bound (~50-100 ms). Calling it
    directly inside an `async def` would stall the *entire* event loop for
    that whole time, so concurrent requests queue behind every login attempt.
    That failure mode is a latency spike under load, not an error, so it
    survives tests and review unless the reason is written down right here.
    See CLAUDE.md, "Two traps this creates".
    """
    return await anyio.to_thread.run_sync(
        _verify_password_blocking, stored_hash, password
    )


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest — see the module docstring for why not argon2."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ------------------------------------------------------------- credentials


async def authenticate_user(
    settings: Settings, username: str, password: str
) -> Principal:
    record = next(
        (u for u in settings.users if u.get("username") == username),
        None,
    )
    # Verify against a dummy hash when the user is unknown so that a missing
    # username and a wrong password take the same time — otherwise response
    # timing enumerates valid usernames.
    stored = record.get("password_hash", "") if record else _DUMMY_HASH
    ok = await verify_password(stored, password)
    if not record or not ok:
        raise AuthError("invalid username or password")
    return Principal(
        subject=username,
        kind="user",
        scopes=tuple(record.get("scopes", ["read"])),
    )


def authenticate_api_key(settings: Settings, raw_key: str) -> Principal:
    digest = hash_api_key(raw_key)
    for entry in settings.api_keys:
        # compare_digest, not ==: a plain string compare returns early on the
        # first differing byte and leaks the prefix through timing.
        if hmac.compare_digest(digest, entry.get("key_hash", "")):
            return Principal(
                subject=entry.get("name", "api-key"),
                kind="api_key",
                scopes=tuple(entry.get("scopes", ["read"])),
            )
    raise AuthError("invalid API key")


# ------------------------------------------------------------------ tokens


def _issue(settings: Settings, principal: Principal, typ: str, ttl: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": principal.subject,
        "kind": principal.kind,
        "scopes": list(principal.scopes),
        "typ": typ,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(
        payload,
        settings.jwt_private_key,
        algorithm="RS256",
        headers={"kid": settings.jwt_kid},
    )


def issue_access_token(settings: Settings, principal: Principal) -> str:
    return _issue(settings, principal, "access", settings.access_ttl_seconds)


def issue_refresh_token(settings: Settings, principal: Principal) -> str:
    return _issue(settings, principal, "refresh", settings.refresh_ttl_seconds)


def decode_token(settings: Settings, token: str, expected_typ: str) -> Principal:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=_ALGORITHMS,
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        # Deliberately not echoing the token or the library's message — a
        # decode error can quote token content into logs and responses.
        raise AuthError("invalid token") from exc

    if payload.get("typ") != expected_typ:
        # Without this a long-lived refresh token would be accepted as an
        # access token, silently defeating the short access TTL.
        raise AuthError(f"expected a {expected_typ} token")

    return Principal(
        subject=payload.get("sub", ""),
        kind=payload.get("kind", "user"),
        scopes=tuple(payload.get("scopes", ["read"])),
    )


# Cost is paid once at import, and only to normalize failure timing above.
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-failure")
