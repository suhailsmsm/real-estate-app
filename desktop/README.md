# Real Estate App New — Windows desktop build

The full analytics platform (React SPA + read-only API + MCP server + LLM
copilot) packaged as a single Windows desktop app. Same pattern as the older
DubaiEstate desktop app: PyInstaller + pywebview (Edge WebView2, no bundled
Chromium) + Inno Setup, built on a free GitHub Actions Windows runner.

## How it works (the one trick worth knowing)

The desktop replaces **no service code**. It runs the REAL `dxb_api`,
`dxb_mcp` and `dxb_copilot` apps on one loopback origin (`shell.py`), with
the API's Postgres engine swapped for a **read-only SQLite snapshot** of the
analytics database. The API's SQLAlchemy queries compile to SQLite unchanged
except for three Postgres-isms, handled as dialect adapters:

| Postgres-ism | Where | SQLite adapter |
|---|---|---|
| `similarity()` (pg_trgm fuzzy search) | dimensions/buildings repos | Python trigram similarity registered as a SQLite custom function (`sqlite_funcs.py`) |
| `ST_AsGeoJSON()` | geo repo | identity over exported GeoJSON text (also registered as `AsGeoJSON` — SQLAlchemy strips the `ST_` schema prefix on SQLite) |
| `current_date + interval 'N years'` | meta repo (one raw `text()` fragment) | statement rewrite in a registered dialect subclass (`db_engine.py`) |

Geography columns are exported as GeoJSON TEXT; Decimals as REAL; dates as
ISO text. `PRAGMA query_only` keeps the API's no-writes guarantee true at the
SQLite level too.

```
RealEstateAppNew.exe
└─ one uvicorn server on 127.0.0.1:<free port from 8600>
   ├─ /              the built SPA (ui/dist)
   ├─ /api/*         dxb_api (SQLite snapshot engine, auth off — loopback only)
   ├─ /mcp/mcp       dxb_mcp (the copilot's data route)
   ├─ /copilot/*     dxb_copilot (LLM provider from user settings)
   ├─ /desktop/*     settings + endpoint test
   └─ /desktop-settings  the settings page
```

## Building

**On GitHub (the normal path):** push a `v*` tag or run the workflow from
the Actions tab — `.github/workflows/build-desktop.yml` tests, builds the
SPA, builds the exe and the installer, uploads the artifact / drafts a
release.

**The data snapshot:** CI cannot rebuild the database (it needs the live DLD
backfill), so it downloads `dxb.db` from the latest release tagged
`data-snapshot-*`. Without one it ships a schema-only db (the app runs and
reports zero coverage honestly). To publish/refresh:

```bash
# from the repo root, stack running and backfill complete:
docker-compose run --rm -v "$(pwd)/desktop:/desktop" \
    elt python /desktop/export_sqlite.py --out /desktop/data/dxb.db

gh release create data-snapshot-v1 desktop/data/dxb.db
```

**Locally on Windows (fallback):** see `real_estate_app.spec`'s docstring —
install the four packages, run PyInstaller, then Inno Setup with
`installer.iss`.

## Copilot model settings

The chat assistant needs an OpenAI-compatible endpoint + key. The user sets
them in the app (Settings, or `http://127.0.0.1:<port>/desktop-settings`):
OpenAI, DeepSeek, a local proxy or Ollama. Stored per-user in
`%APPDATA%/RealEstateAppNew/settings.json`, never echoed back by the API
(masked only). Saving applies live — no restart. An empty API key on save
keeps the stored one.

## Development

```bash
cd desktop
python -m venv .venv && .venv/bin/pip install -e . --no-deps \
    ../packages/dxb-core ../api ../mcp ../copilot \
    fastapi uvicorn aiosqlite greenlet httpx pytest pytest-asyncio asgi-lifespan ruff
.venv/bin/python -m pytest -q          # 13 tests: the whole architecture
.venv/bin/python -m dxb_desktop.launcher   # dev run (browser fallback on macOS)
```

The tests mount the real services against a fixture snapshot — if they pass,
the packaged app works; only row counts differ.

## Files

| File | What |
|---|---|
| `src/dxb_desktop/shell.py` | the single-origin app: mounts the real services, auth shims, settings endpoints, lifespans |
| `src/dxb_desktop/db_engine.py` | SQLite engine: custom functions, dialect rewrite, query_only pragma |
| `src/dxb_desktop/sqlite_funcs.py` | `similarity` + `ST_AsGeoJSON` in Python |
| `src/dxb_desktop/schema.py` | the snapshot's tables/types — shared by exporter and tests |
| `src/dxb_desktop/settings_store.py` | per-user LLM settings (masked GET, live-apply) |
| `src/dxb_desktop/launcher.py` | entry point: free port, uvicorn thread, pywebview window |
| `src/dxb_desktop/make_db.py` | schema-only snapshot (CI fallback, test base) |
| `export_sqlite.py` | runs in the ELT container; exports live Postgres → SQLite |
| `real_estate_app.spec` | PyInstaller (onedir — the db is too big for onefile) |
| `installer.iss` | Inno Setup per-user installer |
| `tests/` | the architecture proofs |
