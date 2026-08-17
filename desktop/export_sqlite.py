#!/usr/bin/env python
"""Export the running Postgres analytics stack into one SQLite snapshot.

RUNS INSIDE THE ELT CONTAINER (it needs psycopg + dxb-core, which only the
ELT image has). From the repo root:

    docker-compose run --rm \
        -v "$(pwd)/desktop:/desktop" \
        elt python /desktop/export_sqlite.py --out /desktop/data/dxb.db

What it copies: every table the READ API touches (see dxb_desktop.schema's
EXPORT_TABLES) — dims, both fact tables, the three marts, area-code
resolution tables and etl_run — with geography columns converted to GeoJSON
text on the way out (PostGIS ST_AsGeoJSON), dates to ISO text, Decimals to
float, arrays to JSON.

What it deliberately does NOT copy: staging tables (multi-GB of raw JSON
payloads), dim_address / dim_location / fact_listing (no API consumer),
alembic_version (meaningless outside Postgres).

The snapshot is a point-in-time copy: refresh it by re-running this script
after the ELT finishes more data, then rebuild the installer.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# The ELT image has dxb (and dxb_core) importable; /desktop/src is not on its
# path, so add it before importing the shared schema module.
sys.path.insert(0, "/desktop/src")

from dxb.db.engine import get_session  # noqa: E402
from dxb_desktop.schema import (  # noqa: E402
    EXPORT_TABLES,
    create_table_ddl,
    index_ddls,
    is_geo_column,
    row_transform,
)

from sqlalchemy import func, select  # noqa: E402


def _select_star_with_geojson(table):
    """A SELECT of every column; Geography columns become ST_AsGeoJSON text."""
    cols = []
    for col in table.columns:
        if is_geo_column(col):
            cols.append(func.ST_AsGeoJSON(col).label(col.key))
        else:
            cols.append(col)
    return select(*cols)


def export(out_path: Path, batch_size: int = 2000) -> None:
    import sqlite3 as _sqlite

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    sqlite = _sqlite.connect(out_path)
    sqlite.execute("PRAGMA journal_mode = OFF")
    sqlite.execute("PRAGMA synchronous = OFF")

    started = time.time()
    with get_session() as session:
        for model in EXPORT_TABLES:
            table = model.__table__
            sqlite.execute(create_table_ddl(model))
            cols = [c.name for c in table.columns]
            placeholders = ", ".join("?" for _ in cols)
            quoted = ", ".join(f'"{c}"' for c in cols)
            insert = f'INSERT INTO "{table.name}" ({quoted}) VALUES ({placeholders})'

            result = session.execute(_select_star_with_geojson(table))
            total = 0
            batch: list[tuple] = []
            while True:
                rows = result.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    batch.append(
                        tuple(row_transform(dict(row._mapping)).values())
                    )
                    total += 1
                sqlite.executemany(insert, batch)
                batch.clear()
            sqlite.commit()
            print(f"{table.name}: {total} rows", flush=True)

        for model in EXPORT_TABLES:
            for stmt in index_ddls(model):
                sqlite.execute(stmt)
        sqlite.commit()

    # Analyze once at the end: the API's fuzzy search and joins rely on the
    # query planner knowing the table shapes.
    sqlite.execute("ANALYZE")
    sqlite.commit()
    sqlite.close()
    print(f"done in {time.time() - started:.1f}s -> {out_path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="/desktop/data/dxb.db",
        help="output SQLite path (default /desktop/data/dxb.db)",
    )
    args = parser.parse_args()
    export(Path(args.out))
