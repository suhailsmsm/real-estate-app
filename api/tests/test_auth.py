"""Auth: token lifecycle, the rejection paths, and API keys."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from dxb_api import auth
from dxb_api.auth import AuthError, Principal
from dxb_api.config import build_settings

PASSWORD = "correct horse battery staple"


@pytest.fixture
def user_settings(rsa_keypair, monkeypatch):
    priv, pub = rsa_keypair
    monkeypatch.setenv("DXB_JWT_PRIVATE_KEY", priv)
    monkeypatch.setenv("DXB_JWT_PUBLIC_KEY", pub)
    monkeypatch.setenv(
        "DXB_API_USERS",
        json.dumps(
            [{"username": "analyst", "password_hash": auth.hash_password(PASSWORD)}]
        ),
    )
    monkeypatch.setenv(
        "DXB_API_KEYS",
        json.dumps([{"name": "mcp", "key_hash": auth.hash_api_key("secret-key")}]),
    )
    return build_settings()


# ------------------------------------------------------------- passwords


async def test_correct_password_authenticates(user_settings):
    principal = await auth.authenticate_user(user_settings, "analyst", PASSWORD)
    assert principal.subject == "analyst"
    assert principal.kind == "user"


async def test_wrong_password_is_rejected(user_settings):
    with pytest.raises(AuthError):
        await auth.authenticate_user(user_settings, "analyst", "wrong")


async def test_unknown_user_is_rejected(user_settings):
    with pytest.raises(AuthError):
        await auth.authenticate_user(user_settings, "nobody", PASSWORD)


async def test_unknown_user_still_runs_a_hash_verification(user_settings, mocker=None):
    """Timing must not distinguish 'no such user' from 'wrong password',
    otherwise response latency enumerates valid usernames."""
    calls = []
    original = auth._verify_password_blocking

    def spy(stored, password):
        calls.append(stored)
        return original(stored, password)

    auth._verify_password_blocking = spy
    try:
        with pytest.raises(AuthError):
            await auth.authenticate_user(user_settings, "nobody", PASSWORD)
    finally:
        auth._verify_password_blocking = original

    assert len(calls) == 1
    assert calls[0] == auth._DUMMY_HASH


async def test_password_verification_runs_off_the_event_loop(
    user_settings, monkeypatch
):
    """argon2 is ~50-100ms of CPU by design; running it inline would stall
    every concurrent request (CLAUDE.md, 'Two traps this creates')."""
    seen = {}
    import anyio.to_thread

    original = anyio.to_thread.run_sync

    async def spy(fn, *args, **kwargs):
        seen["offloaded"] = True
        return await original(fn, *args, **kwargs)

    monkeypatch.setattr(anyio.to_thread, "run_sync", spy)
    await auth.verify_password(auth.hash_password("x"), "x")
    assert seen.get("offloaded") is True


def test_argon2id_is_the_hash_variant_used():
    assert auth.hash_password("x").startswith("$argon2id$")


# ------------------------------------------------------------- API keys


def test_valid_api_key_authenticates(user_settings):
    principal = auth.authenticate_api_key(user_settings, "secret-key")
    assert principal.subject == "mcp"
    assert principal.kind == "api_key"


def test_invalid_api_key_is_rejected(user_settings):
    with pytest.raises(AuthError):
        auth.authenticate_api_key(user_settings, "not-the-key")


def test_api_keys_are_not_stored_in_plaintext(user_settings):
    assert all("secret-key" not in json.dumps(e) for e in user_settings.api_keys)


# ---------------------------------------------------------------- tokens


def test_access_token_round_trips(user_settings):
    token = auth.issue_access_token(user_settings, Principal("analyst", "user"))
    principal = auth.decode_token(user_settings, token, "access")
    assert principal.subject == "analyst"


def test_token_carries_a_kid_so_keys_can_be_rotated(user_settings):
    token = auth.issue_access_token(user_settings, Principal("analyst", "user"))
    assert jwt.get_unverified_header(token)["kid"] == user_settings.jwt_kid


def test_refresh_token_is_not_accepted_as_an_access_token(user_settings):
    """Without the typ check, a 7-day refresh token would work everywhere and
    the 15-minute access TTL would be meaningless."""
    token = auth.issue_refresh_token(user_settings, Principal("analyst", "user"))
    with pytest.raises(AuthError, match="access"):
        auth.decode_token(user_settings, token, "access")


def test_access_token_is_not_accepted_as_a_refresh_token(user_settings):
    token = auth.issue_access_token(user_settings, Principal("analyst", "user"))
    with pytest.raises(AuthError, match="refresh"):
        auth.decode_token(user_settings, token, "refresh")


def test_expired_token_is_rejected(user_settings):
    payload = {
        "sub": "analyst",
        "typ": "access",
        "iss": user_settings.jwt_issuer,
        "aud": user_settings.jwt_audience,
        "exp": datetime.now(UTC) - timedelta(minutes=1),
    }
    token = jwt.encode(payload, user_settings.jwt_private_key, algorithm="RS256")
    with pytest.raises(AuthError, match="expired"):
        auth.decode_token(user_settings, token, "access")


def test_token_signed_by_another_key_is_rejected(
    user_settings, rsa_keypair, monkeypatch
):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    token = jwt.encode(
        {
            "sub": "attacker",
            "typ": "access",
            "iss": user_settings.jwt_issuer,
            "aud": user_settings.jwt_audience,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        other_pem,
        algorithm="RS256",
    )
    with pytest.raises(AuthError):
        auth.decode_token(user_settings, token, "access")


def test_wrong_audience_is_rejected(user_settings):
    token = jwt.encode(
        {
            "sub": "analyst",
            "typ": "access",
            "iss": user_settings.jwt_issuer,
            "aud": "some-other-service",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        user_settings.jwt_private_key,
        algorithm="RS256",
    )
    with pytest.raises(AuthError):
        auth.decode_token(user_settings, token, "access")


def test_wrong_issuer_is_rejected(user_settings):
    token = jwt.encode(
        {
            "sub": "analyst",
            "typ": "access",
            "iss": "somebody-else",
            "aud": user_settings.jwt_audience,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        user_settings.jwt_private_key,
        algorithm="RS256",
    )
    with pytest.raises(AuthError):
        auth.decode_token(user_settings, token, "access")


def test_alg_none_token_is_rejected(user_settings):
    """The classic JWT forgery: strip the signature and claim `alg: none`."""
    token = jwt.encode(
        {"sub": "attacker", "typ": "access", "aud": user_settings.jwt_audience},
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthError):
        auth.decode_token(user_settings, token, "access")


def test_hs256_token_signed_with_the_public_key_is_rejected(user_settings):
    """The algorithm-confusion attack: an RS256 verification key is public, so
    without an `algorithms` allow-list an attacker signs HS256 *using that
    published public key as the HMAC secret* and the server verifies it.

    Forged by hand: PyJWT refuses to encode HS256 with an asymmetric PEM, so
    using jwt.encode here would test PyJWT's guard rather than ours.
    """
    import base64
    import hashlib
    import hmac as hmac_mod

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(
        json.dumps(
            {
                "sub": "attacker",
                "typ": "access",
                "iss": user_settings.jwt_issuer,
                "aud": user_settings.jwt_audience,
                "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            }
        ).encode()
    )
    signing_input = header + b"." + payload
    signature = b64(
        hmac_mod.new(
            user_settings.jwt_public_key.encode(), signing_input, hashlib.sha256
        ).digest()
    )
    token = (signing_input + b"." + signature).decode()

    with pytest.raises(AuthError):
        auth.decode_token(user_settings, token, "access")


def test_garbage_token_is_rejected(user_settings):
    with pytest.raises(AuthError):
        auth.decode_token(user_settings, "not.a.token", "access")
