"""Fixtures: a tiny SQLite snapshot built from the SHARED schema module, plus
the fully-mounted shell app behind httpx's ASGI transport.

These tests are the proof of the desktop architecture: if the REAL dxb_api
repositories, the REAL dxb_mcp server and the REAL dxb_copilot app all work
through the shell against a fixture database, then the exported snapshot
works too — the only difference is row counts.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """A schema-valid snapshot with a few honest rows across the hot tables.

    Column names must match dxb-core exactly — that is the whole contract the
    desktop runs on. Nullable columns are omitted; NOT NULL ones supplied.
    Geography columns hold GeoJSON text, exactly as the exporter writes it.
    """
    from dxb_desktop.make_db import make_db

    path = tmp_path / "fixture.db"
    make_db(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    ts = "2026-01-01 00:00:00"

    conn.execute(
        "INSERT INTO dim_source (id, code, name, base_url, license,"
        " is_government, created_at) VALUES (1, 'dld_gateway', 'DLD gateway',"
        " 'https://dubailand.gov.ae', NULL, 1, ?)",
        (ts,),
    )
    conn.execute(
        "INSERT INTO dim_area (id, dld_area_code, name_en, centroid,"
        " geo_match_method, geo_source_id, created_at)"
        " VALUES (10, '101', 'MARSA DUBAI',"
        ' \'{"type":"Point","coordinates":[55.14,25.08]}\','
        " 'exact', 1, ?)",
        (ts,),
    )
    conn.execute(
        "INSERT INTO dim_project (id, name_en, area_id, is_master, source_id,"
        " location, geo_match_method, created_at)"
        " VALUES (20, 'MARINA HEIGHTS', 10, 0, 1,"
        ' \'{"type":"Point","coordinates":[55.14,25.09]}\','
        " 'nominatim_validated', ?)",
        (ts,),
    )
    conn.execute(
        "INSERT INTO dim_property_type (id, usage, prop_type, prop_subtype,"
        " created_at) VALUES (30, 'residential', 'Unit', 'Flat', ?)",
        (ts,),
    )

    # A full monthly series (26 months, 2024-01..2026-02): capital growth
    # compares count-weighted 12-month anchor windows at each end, so the
    # series must span enough months for those windows to separate by > 1
    # year (verified: 24 months -> 0.83y -> null CAGR; 26 -> 1.08y -> value).
    from datetime import date as _date

    start = _date(2024, 1, 1)
    for i in range(26):
        month = _date(
            start.year + (start.month - 1 + i) // 12,
            (start.month - 1 + i) % 12 + 1,
            1,
        )
        price = 12000.0 * (1.0 + 0.004 * i)  # ~5% over the series
        rent = 900.0 * (1.0 + 0.002 * i)
        conn.execute(
            "INSERT INTO mart_area_monthly (area_id, month, usage, sale_cnt,"
            " sale_median_price_m2, rent_cnt, rent_median_annual_m2,"
            " gross_yield_pct, created_at)"
            " VALUES (10, ?, 'residential', 5, ?, 5, ?, 8.9, ?)",
            (month.isoformat(), round(price, 2), round(rent, 2), ts),
        )
        conn.execute(
            "INSERT INTO mart_project_monthly (project_id, month, usage,"
            " sale_cnt, sale_median_price_m2, rent_cnt, rent_median_annual_m2,"
            " gross_yield_pct, created_at)"
            " VALUES (20, ?, 'residential', 3, ?, 3, ?, 7.6, ?)",
            (month.isoformat(), round(price * 1.1, 2), round(rent * 1.05, 2), ts),
        )

    # One sale + one rent: facts paging and coverage need real rows.
    conn.execute(
        "INSERT INTO fact_sale_transaction (id, txn_number, txn_date,"
        " txn_group, is_offplan, property_type_id, area_id, project_id,"
        " actual_area_m2, amount_aed, price_per_m2, source_id, source_url,"
        " source_ref, created_at)"
        " VALUES (1, 'T-1', '2026-01-15 00:00:00', 'Sales', 0, 30, 10, 20,"
        " 160.0, 2000000.0, 12500.0, 1, 'https://dubailand.gov.ae', 'R1', ?)",
        (ts,),
    )
    conn.execute(
        "INSERT INTO fact_rent_contract (id, start_date, end_date, version,"
        " property_type_id, area_id, project_id, actual_area_m2,"
        " annual_amount_aed, rent_per_m2_year, source_id, source_url,"
        " source_ref, created_at)"
        " VALUES (1, '2026-01-20', '2027-01-19', 'New', 30, 10, 20, 120.0,"
        " 120000.0, 1000.0, 1, 'https://dubailand.gov.ae', 'C1:1', ?)",
        (ts,),
    )

    conn.execute(
        "INSERT INTO etl_run (id, kind, started_at, finished_at, status,"
        " attempts, created_at) VALUES (1, 'backfill', ?, ?, 'ok', 1, ?)",
        (ts, "2026-01-01 01:00:00", ts),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def ui_dir(tmp_path: Path) -> Path:
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.html").write_text(
        "<!doctype html><title>Real Estate App New</title>", encoding="utf-8"
    )
    return tmp_path / "ui"


@pytest.fixture()
async def client(db_path: Path, ui_dir: Path):
    """ASGI client for the whole shell — one origin, all four services.

    uvicorn runs the shell's lifespan in production (which itself enters the
    mounted apps' lifespans — see shell.build_shell). httpx's ASGITransport
    does not, so asgi-lifespan's LifespanManager runs it here — it owns the
    task that enters AND exits the lifespan, which plain manual
    __aenter__/__aexit__ across pytest setup/teardown cannot (anyio cancel
    scopes are task-bound; entering in setup and exiting in teardown trips
    "Attempted to exit a cancellable scope in a different task").
    """
    import httpx
    from asgi_lifespan import LifespanManager

    from dxb_desktop.shell import build_shell

    app, _swap = build_shell(db_path, ui_dir, port=8600)
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8600"
        ) as c:
            yield c
