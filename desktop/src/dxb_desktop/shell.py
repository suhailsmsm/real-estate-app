"""The desktop shell: ONE origin serving UI + API + MCP + copilot.

Replaces the whole docker/nginx edge with one FastAPI app on 127.0.0.1:

    /                     the built React SPA (static files)
    /api/*                the REAL dxb_api app (SQLite snapshot engine)
    /api/auth/login|refresh  desktop shims (see below)
    /mcp/mcp              the REAL dxb_mcp server, mounted at /mcp
    /mcp/health             (its own health route, under the mount)
    /copilot/*            the REAL dxb_copilot app, rebuilt on settings save
    /desktop/settings     LLM settings (masked GET, live-apply POST)
    /desktop/test         one-token LLM endpoint verification
    /desktop-settings     the settings page (static HTML)

Why the auth shims: the SPA shows a login form whenever it has no refresh
token, and with the API's auth-disabled mode /auth/login answers 401 "no
tokens are issued" — a dead end for a desktop app. The shell intercepts the
two auth routes BEFORE the API mount and issues fixed local tokens; the API
itself (DXB_AUTH_DISABLED semantics) accepts any bearer. Everything stays on
loopback, so this weakens nothing that was exposed.

What is deliberately NOT done: no code changes to dxb_api / dxb_mcp /
dxb_copilot. The desktop is a packaging of the real services — the API's
engine is swapped at one documented monkeypatch, and everything else is
configuration, exactly like the docker deployment configures them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from . import settings_store
from .db_engine import build_sqlite_engine

log = logging.getLogger(__name__)

# A token the SPA stores and sends; never validated (auth is disabled on the
# loopback-only API), but it must be non-empty and stable for restoreSession.
DESKTOP_ACCESS_TOKEN = "desktop-local-session"
DESKTOP_REFRESH_TOKEN = "desktop-local-session"


class _SettingsBody(BaseModel):
    base_url: str
    api_key: str
    model: str
    notes: str = ""


class _SwappableCopilot:
    """ASGI wrapper so POST /desktop/settings can swap in a fresh copilot app
    (its Settings are read at app build time) without restarting anything."""

    def __init__(self) -> None:
        self.app: Any = None

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if self.app is None:  # pragma: no cover - built in build_shell
            raise RuntimeError("copilot app not built yet")
        await self.app(scope, receive, send)


def _build_api_app(db_path: Path) -> FastAPI:
    """The real dxb_api app with its engine swapped for the SQLite snapshot.

    Two wiring details matter:

    - The patch on ``dxb_api.main.build_engine`` stays applied for the app's
      whole lifetime: the lifespan calls it at SERVER STARTUP, not at
      construction time, resolving the name on the module then.
    - ``auth_disabled`` must ALSO be set through the environment (plus a
      ``get_settings.cache_clear()``): the routers' ``SettingsDep`` resolves
      the LRU-cached env-based settings, not the Settings object passed to
      ``create_app`` — only engine/root_path come from that object.
    """
    import os

    import dxb_api.main as api_main
    from dxb_api.config import Settings, get_settings

    os.environ["DXB_AUTH_DISABLED"] = "1"
    get_settings.cache_clear()

    settings = Settings(
        db_host="desktop",  # unused — the engine below replaces any DSN use
        db_port=0,
        db_user="desktop",
        db_password="",
        db_name="snapshot",
        db_pool_size=1,
        db_max_overflow=0,
        db_statement_timeout_ms=15000,
        auth_disabled=True,
        jwt_private_key="",  # never used with auth_disabled
        jwt_public_key="",
        jwt_kid="desktop",
        jwt_issuer="desktop",
        jwt_audience="desktop",
        access_ttl_seconds=1,
        refresh_ttl_seconds=1,
        root_path="/api",  # mirrors the nginx mount: URLs render as /api/...
        users=[],
        api_keys=[],
    )

    engine = build_sqlite_engine(db_path)
    api_main.build_engine = lambda _settings: engine  # noqa: F811 - deliberate
    return api_main.create_app(settings)


def _build_mcp_app(port: int) -> Any:
    """The real dxb_mcp ASGI app, pointed at our own /api over loopback."""
    from dxb_mcp import server as mcp_server
    from dxb_mcp.config import Settings

    settings = Settings(
        api_base_url=f"http://127.0.0.1:{port}/api",
        api_key="",  # the API's auth is disabled on loopback
        api_timeout_seconds=60.0,
        host="127.0.0.1",
        port=0,  # unused: uvicorn is owned by the shell, not the mcp app
        path="/mcp",  # under the /mcp mount -> full path /mcp/mcp
        stdio_enabled=False,
        allowed_hosts=(
            "localhost",
            "127.0.0.1",
            f"localhost:{port}",
            f"127.0.0.1:{port}",
        ),
        allowed_origins=("http://localhost", f"http://127.0.0.1:{port}"),
        auth_disabled=True,  # the only callers are in-process (copilot)
    )
    return mcp_server.build_app(settings)


def _build_copilot_app(port: int, llm: settings_store.LlmSettings) -> FastAPI:
    """The real dxb_copilot app for the CURRENT LLM settings."""
    from dxb_copilot.config import Settings as CopilotSettings
    from dxb_copilot.server import create_app

    settings = CopilotSettings(
        mcp_url=f"http://127.0.0.1:{port}/mcp/mcp",
        mcp_api_key="",  # the MCP server's auth is disabled on loopback
        mcp_timeout_seconds=60.0,
        api_url=f"http://127.0.0.1:{port}/api",  # bearer verification only
        provider="openai",
        model=llm.model,
        max_tokens=4096,
        max_turns=8,
        anthropic_api_key="",
        openai_api_key=llm.api_key,
        ollama_url="http://127.0.0.1:11434",
        host="127.0.0.1",
        port=0,  # unused: uvicorn is owned by the shell
        root_path="/copilot",
        client_api_keys=[],
        auth_disabled=True,  # loopback-only desktop; the SPA's local token
    )
    return create_app(settings)


def build_shell(
    db_path: Path, ui_dir: Path, port: int
) -> tuple[FastAPI, _SwappableCopilot]:
    """Assemble everything. Returns (shell app, copilot swapper) — the
    swapper is returned so the settings endpoint can hot-reload the copilot."""
    # The one MIME entry Python's mimetypes may not know depending on
    # version/platform: the SPA's MapLibre worker is an .mjs module, and a
    # wrong MIME makes the browser silently refuse to run it — a real,
    # documented failure mode of this very project behind nginx (CLAUDE.md).
    import mimetypes
    from contextlib import asynccontextmanager

    mimetypes.add_type("application/javascript", ".mjs")

    # uvicorn runs only THIS app's lifespan — Mounts do not propagate
    # lifespans to sub-apps. The API's installs its sessionmaker and the MCP
    # server's starts the Streamable HTTP task group ("Task group is not
    # initialized" without it), so the shell enters both explicitly.
    api_app = _build_api_app(db_path)
    mcp_app = _build_mcp_app(port)
    # The MCP app is an ApiKeyAuthMiddleware (security.py stores the wrapped
    # app as `_app`) around a Starlette app; only the inner one owns the
    # lifespan that runs the Streamable HTTP session manager.
    mcp_inner = getattr(mcp_app, "_app", None) or getattr(mcp_app, "app", mcp_app)
    if not hasattr(mcp_inner, "router"):  # pragma: no cover - SDK shape change
        mcp_inner = mcp_app

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        api_ctx = api_app.router.lifespan_context(api_app)
        mcp_ctx = mcp_inner.router.lifespan_context(mcp_inner)
        await api_ctx.__aenter__()
        await mcp_ctx.__aenter__()
        try:
            yield {}
        finally:
            await mcp_ctx.__aexit__(None, None, None)
            await api_ctx.__aexit__(None, None, None)

    app = FastAPI(
        title="Real Estate App New",
        version="1.0.0",
        docs_url=None,
        lifespan=_lifespan,
    )

    # --- desktop auth shims (registered BEFORE the /api mount; Starlette
    # matches routes in order, so these win over the sub-app's own routes).
    @app.post("/api/auth/login")
    async def _login(_body: dict[str, Any] | None = None) -> JSONResponse:
        return JSONResponse(
            {
                "access_token": DESKTOP_ACCESS_TOKEN,
                "refresh_token": DESKTOP_REFRESH_TOKEN,
                "token_type": "bearer",
                "expires_in": 365 * 24 * 3600,
            }
        )

    @app.post("/api/auth/refresh")
    async def _refresh(_body: dict[str, Any] | None = None) -> JSONResponse:
        return JSONResponse(
            {
                "access_token": DESKTOP_ACCESS_TOKEN,
                "token_type": "bearer",
                "expires_in": 365 * 24 * 3600,
            }
        )

    # --- the real services.
    app.mount("/api", api_app)

    copilot_swap = _SwappableCopilot()
    copilot_swap.app = _build_copilot_app(port, settings_store.load())
    app.mount("/copilot", copilot_swap)

    app.mount("/mcp", mcp_app)

    # --- desktop settings API.
    @app.get("/desktop/settings")
    async def _get_settings() -> dict[str, Any]:
        return settings_store.masked(settings_store.load())

    @app.post("/desktop/settings")
    async def _save_settings(body: _SettingsBody) -> dict[str, Any]:
        existing = settings_store.load()
        # An empty api_key means "keep the stored one": the GET endpoint never
        # returns it, so a save that only changes the model would otherwise
        # wipe the credential. (To replace a key, paste the new one.)
        llm = settings_store.LlmSettings(
            base_url=body.base_url.strip() or existing.base_url,
            api_key=body.api_key.strip() or existing.api_key,
            model=body.model.strip() or existing.model,
            notes=body.notes.strip(),
        )
        settings_store.save(llm)
        copilot_swap.app = _build_copilot_app(port, llm)
        log.info("copilot settings applied (model=%s)", llm.model)
        return settings_store.masked(llm)

    @app.post("/desktop/test")
    async def _test_llm(body: _SettingsBody) -> dict[str, Any]:
        """One-token call against the candidate endpoint, exactly like the
        old desktop app's Test button: catches bad URL/key/model before the
        user commits them."""
        import httpx

        url = body.base_url.strip().rstrip("/") + "/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {body.api_key.strip()}"},
                    json={
                        "model": body.model.strip(),
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                )
            if res.status_code == 200:
                return {"ok": True, "status": res.status_code}
            return {
                "ok": False,
                "status": res.status_code,
                "error": res.text[:300],
            }
        except httpx.HTTPError as exc:
            return {"ok": False, "status": 0, "error": str(exc)}

    @app.post("/desktop/models")
    async def _list_models(body: _SettingsBody) -> dict[str, Any]:
        """The provider's real model list via GET {base_url}/models, so the
        UI's model dropdown matches whatever key/provider the user has."""
        import httpx

        url = body.base_url.strip().rstrip("/") + "/models"
        key = body.api_key.strip() or settings_store.load().api_key
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.get(
                    url, headers={"Authorization": f"Bearer {key}"}
                )
            if res.status_code == 200:
                data = res.json()
                items = data.get("data") if isinstance(data, dict) else data
                ids = sorted(
                    str(m.get("id") or m.get("name") or "")
                    for m in (items or [])
                    if isinstance(m, dict)
                )
                return {"ok": True, "models": [i for i in ids if i]}
            if res.status_code in (401, 403):
                return {
                    "ok": False,
                    "status": res.status_code,
                    "error": "Authentication failed — the API key is invalid "
                    "or belongs to a different provider than this Base URL.",
                }
            return {"ok": False, "status": res.status_code, "error": res.text[:300]}
        except httpx.HTTPError as exc:
            return {"ok": False, "status": 0, "error": str(exc)}

    # --- static: settings page first, then the SPA (html=True catch-all).
    settings_html = ui_dir / "desktop-settings.html"
    if settings_html.exists():

        @app.get("/desktop-settings", include_in_schema=False)
        async def _settings_page() -> Any:
            from fastapi.responses import FileResponse

            return FileResponse(settings_html)

    app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    return app, copilot_swap
