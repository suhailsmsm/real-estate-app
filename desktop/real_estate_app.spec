# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Real Estate App New (Windows desktop).

ONEDIR, not onefile: the data snapshot (data/dxb.db) is far too large for
onefile's extract-to-temp-per-launch pattern, and a real install dir is where
a multi-hundred-MB database belongs. Inno Setup wraps the directory into a
single installer exe anyway, so the user experience is the same.

Build (on Windows, from the repo root):
    pip install -r desktop/requirements-build.txt
    pip install packages/dxb-core api mcp copilot -e desktop
    pyinstaller desktop/real_estate_app.spec --noconfirm

Output: dist/RealEstateAppNew/RealEstateAppNew.exe + _internal/ + data/.
"""

from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()  # .../real estate/desktop
REPO_ROOT = SPEC_DIR.parent           # .../real estate

datas = [
    # The built SPA (ui/dist) is served by the shell at "/".
    (str(REPO_ROOT / "ui" / "dist"), "ui"),
]

# The data snapshot is copied by CI/the build script AFTER PyInstaller runs
# (it is too big to usefully bundle through Analysis datas, and on a dev
# machine it may not exist yet). real_estate_app.spec's dist tree expects it
# at dist/RealEstateAppNew/data/dxb.db — see build-desktop.yml's copy step.

a = Analysis(
    [str(REPO_ROOT / "desktop" / "src" / "dxb_desktop" / "launcher.py")],
    pathex=[str(REPO_ROOT / "desktop" / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # The four services' modules are imported lazily inside functions
        # (shell.py imports dxb_api/dxb_mcp/dxb_copilot at call time), so
        # PyInstaller's static analysis misses them without these hints.
        "dxb_core.models",
        "dxb_api.main",
        "dxb_api.config",
        "dxb_api.auth",
        "dxb_api.deps",
        "dxb_api.errors",
        "dxb_mcp.server",
        "dxb_mcp.config",
        "dxb_mcp.security",
        "dxb_mcp.projection",
        "dxb_mcp.client",
        "dxb_copilot.server",
        "dxb_copilot.config",
        "dxb_copilot.security",
        "dxb_copilot.agent",
        "dxb_copilot.mcp_client",
        "dxb_copilot.ui_tools",
        "dxb_copilot.providers",
        "dxb_copilot.providers.base",
        "dxb_copilot.providers.openai_provider",
        # uvicorn's pure-Python workers (h11, not the C-extension httptools;
        # no websockets — the shell serves HTTP/SSE only).
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
        "openai",
        # Loaded lazily by SQLAlchemy's dialect registry: the shell builds the
        # engine via `sqlite+<custom>://` and registers a dialect subclassing
        # sqlalchemy.dialects.sqlite.aiosqlite, whose DB-API driver aiosqlite
        # is imported dynamically in import_dbapi() — invisible to static
        # analysis (and greenlet is aiosqlite's async requirement).
        "aiosqlite",
        "greenlet",
    ],
    excludes=[
        # Heavy stdlib the app never uses.
        "tkinter", "turtledemo", "turtle", "curses", "pydoc_data",
        # Test-only tooling.
        "pytest", "_pytest",
        # LLM SDKs the desktop never selects: providers import lazily
        # (copilot/.../providers/__init__.py) and the shell hardcodes
        # provider="openai".
        "anthropic", "ollama",
        # The shell has no WebSocket endpoints (SPA is static, copilot is
        # SSE, MCP is Streamable HTTP), so uvicorn never needs websockets.
        "websockets",
        # The desktop engine is SQLite-only: build_engine is monkeypatched
        # (shell.py), so the Postgres driver + libpq + its krb5/ldap/gssapi
        # link deps are never used. PyInstaller pulls them via SQLAlchemy's
        # dialect hooks.
        "psycopg", "psycopg_binary", "psycopg2",
        # uvicorn falls back to the pure-Python h11 protocol (the explicit
        # h11_impl hiddenimport) when httptools is absent.
        "httptools",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RealEstateAppNew",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # AV false positives + marginal gains; not worth it here
    console=False,  # GUI app — no console window
    disable_windowed_traceback=False,
    icon=str(REPO_ROOT / "desktop" / "app.ico") if (REPO_ROOT / "desktop" / "app.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="RealEstateAppNew",
)
