"""The desktop's SQLite async engine, patched into the real API app.

The API's repository layer is plain SQLAlchemy Core/ORM selects over the
dxb-core models — backend-agnostic SQL except for three function calls
(``similarity``, ``ST_AsGeoJSON``, ``date``), of which the first two are
provided as SQLite custom functions (sqlite_funcs.py). So instead of
forking the API, the desktop shell builds the REAL ``dxb_api`` app and
replaces only its engine: one monkeypatch, at one import site, documented
here.

``PRAGMA query_only`` preserves the API's own no-writes guarantee: even if
some future code path tried an INSERT, SQLite itself would refuse.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from . import sqlite_funcs

log = logging.getLogger(__name__)

# The API has exactly ONE raw Postgres SQL fragment (repositories/meta.py's
# `current_date + interval 'N years'`), which SQLite cannot even parse.
# before_cursor_execute's return value does NOT replace the statement
# (verified empirically), so the rewrite lives where the statement actually
# flows: a SQLite dialect subclass, registered through SQLAlchemy's dialect
# registry — the documented extension point for exactly this.
_PG_INTERVAL = re.compile(r"current_date \+ interval '(\d+) years'")


def _rewrite_sql(sql: str) -> str:
    return _PG_INTERVAL.sub(lambda m: f"date('now', '+{m.group(1)} years')", sql)


def _register_dialect() -> str:
    """Register the rewriting aiosqlite dialect; returns its URL scheme."""
    from sqlalchemy.dialects import registry
    from sqlalchemy.dialects.sqlite.aiosqlite import SQLiteDialect_aiosqlite

    class RewritingSQLiteDialect(SQLiteDialect_aiosqlite):
        def do_execute(self, cursor, statement, parameters, context=None):
            cursor.execute(_rewrite_sql(statement), parameters)

    # Idempotent across repeated build_sqlite_engine calls in one process.
    scheme = "dxbrewrite"
    try:
        registry.register(
            f"sqlite.{scheme}", "dxb_desktop.db_engine", "RewritingSQLiteDialect"
        )
    except Exception:  # pragma: no cover - double registration
        pass
    # registry.register needs the class importable by that path — expose it
    # at module level under that exact name.
    globals()["RewritingSQLiteDialect"] = RewritingSQLiteDialect
    return scheme


_URLEND = _register_dialect()


def build_sqlite_engine(db_path: Path) -> AsyncEngine:
    engine: AsyncEngine = create_async_engine(
        f"sqlite+{_URLEND}:///{db_path}",
        # A desktop app is one user at a time; no pool is needed and a
        # single connection keeps the custom-function registration simple.
        poolclass=__import__("sqlalchemy.pool", fromlist=["NullPool"]).NullPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        sqlite_funcs.register_all(dbapi_connection)
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA query_only = ON")
        cur.execute("PRAGMA foreign_keys = OFF")  # snapshot: no FK enforcement
        cur.close()

    return engine


def check_schema(db_path: Path) -> None:
    """Fail fast with a helpful message if the snapshot is missing/empty."""
    if not db_path.exists():
        raise FileNotFoundError(
            f"Data snapshot not found at {db_path}. The desktop app ships a "
            "SQLite snapshot of the analytics database next to the exe "
            "(data/dxb.db). Build it with desktop/export_sqlite.py against "
            "the running stack, or see desktop/README.md."
        )
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        have = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    from .schema import table_names

    missing = [t for t in table_names() if t not in have]
    if missing:
        raise RuntimeError(
            f"Data snapshot {db_path} is missing tables: {', '.join(missing)}. "
            "Re-export it with desktop/export_sqlite.py."
        )
    # Sanity: the query_only pragma applies to API connections only; the
    # check above opened read-write. Keep it that way — this probe never
    # writes either.
    _ = text  # (import kept for future schema probes)
