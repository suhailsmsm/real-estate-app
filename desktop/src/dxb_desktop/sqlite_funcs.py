"""The two Postgres-only SQL functions the API's SQL uses, in Python.

Registered with SQLite as custom functions at connection time (see
`build_engine` in this package). This is what lets the REAL repository code
run unmodified against the exported snapshot: `func.similarity(a, b)` and
`func.ST_AsGeoJSON(x)` compile to plain SQL function calls that SQLite then
routes to these implementations.

- ``similarity`` reimplements pg_trgm's trigram similarity closely enough
  for the API's fuzzy entity search: lowercase, keep alphanumerics, pad each
  word, build the trigram SET, return |A∩B| / |A∪B|. The exact pg_trgm
  scoring differences at the margin do not matter here — the caller compares
  against a threshold (default 0.30) and an ambiguity margin (0.05), and the
  dimension tables are small (428 areas, ~3.6k projects).
- ``ST_AsGeoJSON`` is an identity function: the exporter already stored
  GeoJSON text in the geography columns, so "serializing" it means returning
  it unchanged. (PostGIS's version also has precision/version arguments the
  API never passes.)
- ``date`` exists natively in SQLite; nothing to do.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def trigrams(text: str) -> set[str]:
    """pg_trgm-style trigram set: per word, padded with two leading spaces
    and one trailing space, exactly like Postgres does it."""
    out: set[str] = set()
    for word in _NON_ALNUM.sub(" ", (text or "").lower()).split():
        padded = f"  {word} "
        for i in range(len(padded) - 2):
            out.add(padded[i : i + 3])
    return out


def similarity(a: str | None, b: str | None) -> float:
    """pg_trgm similarity(a, b) ∈ [0, 1]."""
    if not a or not b:
        return 0.0
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def st_asgeojson(value: str | None) -> str | None:
    """Identity: the snapshot's geography columns already hold GeoJSON."""
    return value


def register_all(dbapi_connection: object) -> None:
    """Register every custom function on one connection.

    Called from SQLAlchemy's ``connect`` event. For ``sqlite+aiosqlite`` the
    listener receives SQLAlchemy's connection adapter, whose method calls run
    through ``await_only`` — and sync-style event listeners execute inside
    exactly the greenlet that makes that work during pool connect. Verified
    against aiosqlite in this package's tests (`test_similarity_sql` runs
    through a real engine).
    """
    create_function = getattr(dbapi_connection, "create_function", None)
    if create_function is None:  # pragma: no cover - unexpected DB-API shape
        return
    # Both spellings: SQLAlchemy parses `func.ST_AsGeoJSON` as schema "ST" +
    # name "AsGeoJSON", and the SQLite dialect renders functions without a
    # schema prefix — so the API's own calls arrive as "AsGeoJSON(...)"
    # (discovered live; SQLite name matching is case-insensitive, so one
    # canonical spelling covers the rest).
    create_function("similarity", 2, similarity)
    create_function("ST_AsGeoJSON", 1, st_asgeojson)
    create_function("AsGeoJSON", 1, st_asgeojson)
