"""Inbound auth. This service spends money per request, so the only acceptable
direction to be wrong in is refusing."""

from __future__ import annotations

from dxb_copilot.config import Settings
from dxb_copilot.security import authenticate, hash_key


def settings(**over) -> Settings:
    base = dict(
        mcp_url="http://mcp:8100/mcp",
        api_url="http://api:8000",
        mcp_api_key="",
        mcp_timeout_seconds=60.0,
        provider="anthropic",
        anthropic_api_key="sk-test",
        openai_api_key="",
        ollama_url="",
        model="claude-sonnet-5",
        max_tokens=1024,
        max_turns=8,
        host="0.0.0.0",
        port=8200,
        client_api_keys=[],
        auth_disabled=False,
    )
    base.update(over)
    return Settings(**base)


def test_rejects_when_no_keys_are_configured():
    # Fails CLOSED. An empty allow-list must not mean "allow everything".
    assert authenticate(settings(), "anything") is False


def test_rejects_a_missing_key():
    s = settings(client_api_keys=[{"name": "ui", "key_hash": hash_key("secret")}])
    assert authenticate(s, None) is False


def test_accepts_a_configured_key():
    s = settings(client_api_keys=[{"name": "ui", "key_hash": hash_key("secret")}])
    assert authenticate(s, "secret") is True


def test_rejects_a_wrong_key():
    s = settings(client_api_keys=[{"name": "ui", "key_hash": hash_key("secret")}])
    assert authenticate(s, "guess") is False


def test_accepts_any_of_several_keys():
    s = settings(
        client_api_keys=[
            {"name": "ui", "key_hash": hash_key("one")},
            {"name": "cli", "key_hash": hash_key("two")},
        ]
    )
    assert authenticate(s, "two") is True


def test_disabled_auth_lets_anything_through():
    # The local-dev escape hatch, which config.py warns loudly about.
    assert authenticate(settings(auth_disabled=True), None) is True


def test_configured_property_tracks_the_model_key():
    assert settings().configured is True
    assert settings(anthropic_api_key="").configured is False
