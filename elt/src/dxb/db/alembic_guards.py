"""Existence guards for Alembic migrations.

Why these exist: migration 0001 builds the schema with
``Base.metadata.create_all()``, so a FRESH volume is created at the CURRENT
model shape. Every later migration that adds a column or table the models
have since gained then collides (``DuplicateColumn`` / ``DuplicateTable``) —
a long-lived volume migrated incrementally as each change landed never hits
this, which is why it went unnoticed. Guards make each additive op a no-op
when the thing it adds is already present, which is also exactly what keeps
them safe on an incremental volume: nothing that should run is skipped,
only what already exists is skipped.

Rule for future migrations: guard additive schema ops (``add_column``,
table-create) and any drop that mirrors a guarded add. Never guard an op
that carries data changes — those must always run.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect


def has_table(bind: Any, name: str) -> bool:
    """True if ``name`` already exists in the target schema."""
    return bool(inspect(bind).has_table(name))


def has_column(bind: Any, table: str, column: str) -> bool:
    """True if ``table`` exists and already has ``column``."""
    insp = inspect(bind)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))
