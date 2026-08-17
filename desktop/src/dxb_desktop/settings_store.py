"""Per-user LLM settings for the desktop copilot, stored as JSON.

The copilot needs an OpenAI-compatible chat endpoint. On the user's Windows
machine that is whatever they have an API key for (OpenAI itself, DeepSeek,
or a local proxy such as cli-proxy-api / LM Studio / Ollama's OpenAI
compatibility layer). The old DubaiEstate desktop app used the same pattern
(.ai.env); here it is a single settings.json under %APPDATA%.

Rules:
- The API key is NEVER returned by the GET endpoint — only a masked preview.
- Saving applies live: the shell rebuilds the copilot app with fresh
  settings (see shell.py), no process restart.
- `configured` mirrors the copilot's own /health semantics: a missing key
  means the app runs but chat explains what is missing instead of crashing.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR_NAME = "RealEstateAppNew"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4.1"


@dataclass
class LlmSettings:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    # Informational only — shown on the settings page so the user remembers
    # where this snapshot came from.
    notes: str = field(default="")


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if not base:
        base = str(Path.home() / ".config")
    d = Path(base) / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def load() -> LlmSettings:
    path = settings_path()
    if not path.exists():
        return LlmSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return LlmSettings()
    known = {f for f in LlmSettings.__dataclass_fields__}
    return LlmSettings(**{k: v for k, v in raw.items() if k in known})


def save(settings: LlmSettings) -> None:
    path = settings_path()
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    # Best effort: keep the key private from other users on shared machines.
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows ACLs differ; non-fatal
        pass


def masked(settings: LlmSettings) -> dict:
    """The GET /desktop/settings payload: everything except the raw key."""
    out = asdict(settings)
    out["api_key_masked"] = _mask(settings.api_key)
    out["configured"] = bool(settings.api_key)
    out["settings_path"] = str(settings_path())
    del out["api_key"]
    return out


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "*" * len(key)
    return f"{key[:6]}…{key[-4:]} ({len(key)} chars)"
