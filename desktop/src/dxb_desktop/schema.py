"""Shared SQLite schema knowledge for the desktop build.

Two consumers, one source of truth:

- ``export_sqlite.py`` (runs inside the ELT container, where psycopg and
  dxb-core live) uses the table list and the type mapping to copy a snapshot
  of the Postgres analytics schema into a single SQLite file.
- the desktop shell's tests use the same DDL to build a tiny fixture
  database, so the fixture can never drift from what the exporter produces.

The desktop API does NOT use this module at runtime: it queries the exported
tables through the *original* dxb-core models, exactly like the real API
does against Postgres — same table names, same column names, so the same
SQLAlchemy statements compile to SQLite unchanged. Only three Postgres-only
functions appear in that SQL (``similarity``, ``ST_AsGeoJSON``, ``date``),
and the first two are re-implemented in Python and registered with SQLite as
custom functions at connect time (see ``sqlite_funcs.py``).

Geography columns (geoalchemy2 ``Geography``) are exported as GeoJSON TEXT —
the exporter selects ``ST_AsGeoJSON(col)`` on the Postgres side, and the API's
own ``ST_AsGeoJSON(...)`` calls on the desktop side become an identity
function over that text.
"""

from __future__ import annotations

from typing import Any

# Every dxb-core table the READ API touches. Deliberately explicit rather
# than "everything": staging tables (multi-GB), dim_address/dim_location (the
# API never reads them) and alembic_version have no desktop consumer, and
# skipping them is the difference between a shippable and an unshippable exe.
from dxb_core.models import (
    AreaCodeEvidence,
    DimArea,
    DimBuilding,
    DimDeveloper,
    DimProject,
    DimPropertyType,
    DimSource,
    EtlRun,
    EtlSourceCutover,
    FactRentContract,
    FactSaleTransaction,
    MartAreaMonthly,
    MartBuildingSummary,
    MartProjectMonthly,
    ProjectAreaActual,
)
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.sql import visitors

EXPORT_TABLES: list[Any] = [
    DimSource,
    DimArea,
    DimDeveloper,
    DimProject,
    DimBuilding,
    DimPropertyType,
    FactSaleTransaction,
    FactRentContract,
    MartAreaMonthly,
    MartProjectMonthly,
    MartBuildingSummary,
    AreaCodeEvidence,
    ProjectAreaActual,
    EtlRun,
    EtlSourceCutover,
]

# PostgreSQL/dxb-core column types -> SQLite column types. Geography is
# handled structurally (via geoalchemy2 import) in `sqlite_type`, not by
# class, to avoid importing geoalchemy2 here when it is merely typechecked.
_SQLITE_TYPES: dict[type, str] = {
    BigInteger: "INTEGER",
    Integer: "INTEGER",
    SmallInteger: "INTEGER",
    Boolean: "INTEGER",
    Text: "TEXT",
    Numeric: "REAL",  # analytics precision; SQLite floats are fine for display
    Date: "TEXT",  # ISO 'YYYY-MM-DD' — sorts correctly as text
    DateTime: "TEXT",  # ISO 'YYYY-MM-DD HH:MM:SS'
}

# geoalchemy2.Geography instances, detected by class name so this module
# doesn't need the geoalchemy2 import when only used from tests.
_GEO_CLASS_NAMES = {"Geography", "Geometry"}


def is_geo_column(col: Any) -> bool:
    return type(col.type).__name__ in _GEO_CLASS_NAMES


def sqlite_type(col: Any) -> str:
    """SQLite column DDL type for a dxb-core column."""
    if is_geo_column(col):
        return "TEXT"  # GeoJSON, produced by the exporter's ST_AsGeoJSON
    for py_type, sqlite in _SQLITE_TYPES.items():
        if isinstance(col.type, py_type):
            return sqlite
    if isinstance(col.type, (ARRAY, JSONB)):
        return "TEXT"  # JSON-encoded array/object
    # Unknown exotic type: text is the lossless fallback.
    return "TEXT"


def _geo_columns(table: Any) -> list[str]:
    return [c.key for c in table.columns if is_geo_column(c)]


def geo_columns() -> dict[str, list[str]]:
    """{table_name: [geo column keys]} — the columns exported as GeoJSON."""
    return {t.__table__.name: _geo_columns(t.__table__) for t in EXPORT_TABLES}


def create_table_ddl(table: Any) -> str:
    """One CREATE TABLE statement for SQLite, mirroring the model's columns.

    Constraints are deliberately loose (no FKs): the exported file is a
    read-only snapshot, and skipping FK enforcement keeps bulk load fast and
    avoids ordering dependencies between tables. Indexes — the part that
    actually matters for query speed — are created separately.
    """
    t = table.__table__
    cols = ", ".join(f'"{c.name}" {sqlite_type(c)}' for c in t.columns)
    return f'CREATE TABLE IF NOT EXISTS "{t.name}" ({cols})'


def index_ddls(table: Any) -> list[str]:
    """CREATE INDEX statements: one per foreign-key column, plus the model's
    own unique constraints (they are correctness-relevant uniqueness and
    usually also the hot lookup path)."""
    t = table.__table__
    stmts: list[str] = []
    seen: set[str] = set()
    for fk in t.foreign_keys:
        col = fk.parent
        if col.name in seen:
            continue
        seen.add(col.name)
        stmts.append(
            f'CREATE INDEX IF NOT EXISTS "ix_{t.name}_{col.name}" '
            f'ON "{t.name}" ("{col.name}")'
        )
    for constraint in t.constraints:
        cname = type(constraint).__name__
        if cname != "UniqueConstraint":
            continue
        # Composite unique constraints are kept composite.
        cols = [c.name for c in getattr(constraint, "columns", [])]
        if not cols:
            continue
        joined = "_".join(cols)
        stmts.append(
            f'CREATE UNIQUE INDEX IF NOT EXISTS "uq_{t.name}_{joined}" '
            f'ON "{t.name}" ({", ".join(chr(34) + c + chr(34) for c in cols)})'
        )
    return stmts


def create_all(conn: Any) -> None:
    """Create every exported table (+indexes) on a stdlib sqlite3 connection."""
    for table in EXPORT_TABLES:
        conn.execute(create_table_ddl(table))
    for table in EXPORT_TABLES:
        for stmt in index_ddls(table):
            conn.execute(stmt)
    conn.commit()


def table_names() -> list[str]:
    return [t.__table__.name for t in EXPORT_TABLES]


def row_transform(row: dict[str, Any]) -> dict[str, Any]:
    """Make one exporter row sqlite-storable: dates to text, Decimals to
    float, arrays to JSON. Geo columns arrive already as GeoJSON text."""
    import datetime
    import decimal

    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime.datetime):
            out[key] = value.isoformat(sep=" ")
        elif isinstance(value, datetime.date):
            out[key] = value.isoformat()
        elif isinstance(value, decimal.Decimal):
            out[key] = float(value)
        elif isinstance(value, (list, dict)):
            out[key] = __import__("json").dumps(value)
        else:
            out[key] = value
    return out


# Re-exported for the exporter: silence "unused import" while keeping
# `visitors` available for future type-walking without another import site.
_ = visitors
