"""pg_trgm extension + GIN trigram indexes for fuzzy entity search

Backs the API's `?q=` name matching (docs/API_DESIGN.md §7). Without these,
`similarity(name_en, :q) >= threshold` degenerates into a sequential scan
computing trigrams for every row — tolerable on 428 areas, not on 3.6k
projects once it is on the hot path of every map interaction.

`gin_trgm_ops` is the operator class that makes the trigram index usable by
`%` and by the similarity operators. The extension was previously created by
hand on the live database; CREATE EXTENSION IF NOT EXISTS makes that
retroactively reproducible on a from-zero volume.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-24
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_area_name_trgm", "dim_area", "name_en"),
    ("ix_project_name_trgm", "dim_project", "name_en"),
    ("ix_developer_name_trgm", "dim_developer", "name_en"),
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, table, column in _INDEXES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {name} "
            f"ON {table} USING gin ({column} gin_trgm_ops)"
        )


def downgrade() -> None:
    for name, _table, _column in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    # The extension is deliberately left in place: dropping it would break any
    # other trigram index and it is harmless when unused.
