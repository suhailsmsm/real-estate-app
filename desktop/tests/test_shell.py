"""The architecture proofs, end to end through the mounted shell.

Each test exercises a REAL service through the shell — if these pass against
a fixture snapshot, the Windows exe's behavior against the exported snapshot
differs only in row counts.
"""

from __future__ import annotations

import sqlite3

import pytest

# ---------------------------------------------------------------- schema


def test_make_db_creates_every_table(tmp_path):
    from dxb_desktop.make_db import make_db
    from dxb_desktop.schema import table_names

    db = tmp_path / "fresh.db"
    make_db(db)
    with sqlite3.connect(db) as conn:
        have = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    missing = [t for t in table_names() if t not in have]
    assert not missing, f"missing tables: {missing}"


def test_similarity_matches_pg_trgm_shape():
    from dxb_desktop.sqlite_funcs import similarity

    # identical -> 1.0; disjoint -> 0.0; case/punctuation insensitive.
    assert similarity("MARSA DUBAI", "marsa dubai") == 1.0
    assert similarity("MARSA DUBAI", "zzz qqq") == 0.0
    assert 0.0 < similarity("MARSA DUBAI", "MARINA DUBAI") < 1.0


def test_similarity_sql_roundtrip(tmp_path):
    """The custom functions really are callable from SQL through the engine."""
    import asyncio

    from sqlalchemy import text

    from dxb_desktop.db_engine import build_sqlite_engine

    async def run():
        # A nonexistent file is fine: SQLite creates it, nothing is written
        # (query_only), and the fixture uses a real file elsewhere.
        engine = build_sqlite_engine(tmp_path / "funcs.db")
        async with engine.connect() as conn:
            v = await conn.scalar(
                text("SELECT similarity(:a, :b)"),
                {"a": "MARSA DUBAI", "b": "marsa dubai"},
            )
            g = await conn.scalar(text("SELECT ST_AsGeoJSON(:g)"), {"g": '{"a":1}'})
        await engine.dispose()
        return v, g

    value, geo = asyncio.run(run())
    assert value == 1.0
    assert geo == '{"a":1}'


# ------------------------------------------------------------- the shell


@pytest.mark.asyncio
async def test_spa_served(client):
    res = await client.get("/")
    assert res.status_code == 200
    assert "Real Estate App New" in res.text


@pytest.mark.asyncio
async def test_desktop_login_shim(client):
    """The SPA's login flow gets tokens even though the API's auth is off."""
    res = await client.post(
        "/api/auth/login", json={"username": "any", "password": "any"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["access_token"]
    assert body["refresh_token"]


@pytest.mark.asyncio
async def test_api_meta_coverage(client):
    res = await client.get("/api/meta/coverage")
    assert res.status_code == 200
    data = res.json()
    assert data["datasets"]["sale_transactions"]["row_count"] == 1
    assert data["datasets"]["rent_contracts"]["row_count"] == 1


@pytest.mark.asyncio
async def test_api_dimension_search_uses_similarity(client):
    """The REAL repository's pg_trgm fuzzy search, via the Python shim."""
    res = await client.get("/api/dimensions/areas", params={"q": "marsa"})
    assert res.status_code == 200
    names = [a["name_en"] for a in res.json()["items"]]
    assert "MARSA DUBAI" in names


@pytest.mark.asyncio
async def test_api_analytics_growth(client):
    res = await client.get("/api/analytics/growth", params={"entity": "area", "id": 10})
    assert res.status_code == 200
    data = res.json()
    assert data.get("id") == 10 or data.get("entity_id") == 10


@pytest.mark.asyncio
async def test_api_area_ranking(client):
    """The MCP rank_entities tool's underlying endpoint, end to end."""
    res = await client.get(
        "/api/analytics/area-ranking",
        params={
            "entity": "area",
            "metric": "total_return",
            "limit": 5,
            "min_sample": 1,  # fixture rows are few; default gate is 20
        },
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert items and items[0]["name_en"] == "MARSA DUBAI"


@pytest.mark.asyncio
async def test_api_geo_geojson_roundtrip(client):
    """ST_AsGeoJSON identity over exported GeoJSON text — the map's data."""
    res = await client.get("/api/geo/areas")
    assert res.status_code == 200
    features = res.json().get("features", [])
    assert any(f["geometry"] and f["geometry"]["type"] == "Point" for f in features)


@pytest.mark.asyncio
async def test_mcp_tools_list_and_call(client):
    """The REAL MCP server through the shell mount, JSON-RPC over HTTP."""
    res = await client.post(
        "/mcp/mcp",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
    )
    assert res.status_code == 200
    assert "dubai-estate" in res.text

    res = await client.post(
        "/mcp/mcp",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert res.status_code == 200
    assert "rank_entities" in res.text


@pytest.mark.asyncio
async def test_copilot_health(client):
    res = await client.get("/copilot/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_desktop_settings_roundtrip(client, tmp_path, monkeypatch):
    from dxb_desktop import settings_store

    # Point the store at a temp dir so the test never touches real user data.
    monkeypatch.setattr(settings_store, "user_data_dir", lambda: tmp_path)

    # Save with a key, then save WITHOUT one: the key must survive.
    first = {
        "base_url": "http://localhost:9/v1",
        "api_key": "sk-test-123456",
        "model": "test-model",
        "notes": "",
    }
    res = await client.post("/desktop/settings", json=first)
    assert res.status_code == 200
    assert res.json()["configured"] is True
    assert "sk-test" not in res.text  # never echoes the key back

    res = await client.post(
        "/desktop/settings",
        json={**first, "api_key": ""},  # empty = keep the stored one
    )
    assert res.json()["configured"] is True

    got = await client.get("/desktop/settings")
    assert got.json()["model"] == "test-model"
    assert "api_key" not in got.json()
