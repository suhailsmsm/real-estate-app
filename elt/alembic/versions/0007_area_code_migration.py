"""Area code migration support

DLD re-coded ~89 established communities under new area codes starting
2026-07-20 (docs/AREA_CODE_MIGRATION_ANALYSIS.md) — same projects/buildings,
new dld_area_code going forward. Adds:

- dim_area.superseded_by_area_id: nullable self-FK, old row -> new row.
  Purely additive — no existing row's other columns change, no fact/dim row
  referencing an old area is ever rewritten. Only set once a matching
  area_code_evidence row is human-reviewed.
- area_code_evidence: audit table for the detector's findings (project/
  building overlap, transaction counts, first-seen date, reviewed gate).
  Never consulted by aggregation/resolution directly — only the pointer is.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from dxb.db.alembic_guards import has_column, has_table
from dxb_core.models import Base

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Guarded (dxb.db.alembic_guards): superseded_by_area_id is genuinely new
    # even on a fresh volume (0008 later drops it, so the current models lack
    # it) — the add runs; the table create is a no-op there.
    if not has_column(bind, "dim_area", "superseded_by_area_id"):
        op.add_column(
            "dim_area",
            sa.Column(
                "superseded_by_area_id",
                sa.Integer(),
                sa.ForeignKey("dim_area.id"),
                nullable=True,
            ),
        )
    if not has_table(bind, "area_code_evidence"):
        Base.metadata.tables["area_code_evidence"].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["area_code_evidence"].drop(bind=bind)
    op.drop_column("dim_area", "superseded_by_area_id")
