"""Provider selection and per-provider settings.

No `conftest.py` clears env for this package (unlike elt/'s), so every test
here explicitly clears the vars it cares about rather than assuming a clean
slate.
"""

from __future__ import annotations

import pytest

from dxb_copilot.config import build_settings

_PROVIDER_ENV_VARS = (
    "DXB_COPILOT_PROVIDER",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "DXB_COPILOT_OLLAMA_URL",
    "DXB_COPILOT_MODEL",
)


def _clear(monkeypatch):
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Not under test here — silence the unrelated "no client keys" warning.
    monkeypatch.setenv("DXB_COPILOT_AUTH_DISABLED", "1")


def test_default_provider_is_anthropic_preserving_current_behavior(monkeypatch):
    _clear(monkeypatch)
    settings = build_settings()
    assert settings.provider == "anthropic"
    assert settings.model == "claude-sonnet-5"


@pytest.mark.parametrize(
    "provider,key_var,key_val,expected",
    [
        ("anthropic", "ANTHROPIC_API_KEY", "sk-test", True),
        ("anthropic", "ANTHROPIC_API_KEY", "", False),
        ("openai", "OPENAI_API_KEY", "sk-test", True),
        ("openai", "OPENAI_API_KEY", "", False),
    ],
)
def test_configured_reflects_the_selected_providers_credential(
    monkeypatch, provider, key_var, key_val, expected
):
    _clear(monkeypatch)
    monkeypatch.setenv("DXB_COPILOT_PROVIDER", provider)
    if key_val:
        monkeypatch.setenv(key_var, key_val)
    assert build_settings().configured is expected


def test_ollama_is_configured_by_default_no_key_needed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DXB_COPILOT_PROVIDER", "ollama")
    settings = build_settings()
    assert settings.configured is True
    assert settings.ollama_url == "http://host.docker.internal:11434"


@pytest.mark.parametrize(
    "provider,expected_model",
    [
        ("anthropic", "claude-sonnet-5"),
        ("openai", "gpt-4.1"),
        ("ollama", "glm-5.2:cloud"),
    ],
)
def test_model_default_is_provider_specific(monkeypatch, provider, expected_model):
    # A hardcoded Anthropic model name would be meaningless once another
    # provider is selected — this is the regression this test guards.
    _clear(monkeypatch)
    monkeypatch.setenv("DXB_COPILOT_PROVIDER", provider)
    assert build_settings().model == expected_model


def test_model_override_wins_for_any_provider(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DXB_COPILOT_PROVIDER", "ollama")
    monkeypatch.setenv("DXB_COPILOT_MODEL", "qwen2.5")
    assert build_settings().model == "qwen2.5"


def test_misspelled_provider_falls_back_instead_of_crashing(monkeypatch):
    # build_settings() runs at container import time (server.py's
    # module-level `app = create_app()`) — a bare dict-index on an invalid
    # value here would crash-loop the container over a typo'd env var,
    # exactly what Settings.configured's fail-soft design exists to avoid.
    _clear(monkeypatch)
    monkeypatch.setenv("DXB_COPILOT_PROVIDER", "anthropik")
    settings = build_settings()  # must not raise
    assert settings.provider == "anthropic"


def test_missing_credential_hint_matches_the_selected_provider(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DXB_COPILOT_PROVIDER", "openai")
    assert build_settings().missing_credential_hint == "OPENAI_API_KEY"
