"""Area code migration, revision 2: project-anchored, not area-anchored

Revision 1 (0007) added a scalar `dim_area.superseded_by_area_id` assuming
one old area maps to one new area. Running the detector against live data
found that's wrong for 44% of split old areas (MARSA DUBAI alone splits into
4 disjoint communities) — a scalar pointer cannot represent one-to-many.
See docs/AREA_CODE_MIGRATION_ANALYSIS.md.

Revision 2: drop the scalar pointer. Each new area code's projects are
disjoint (measured), so resolution is anchored on the PROJECT instead, which
is genuinely one-to-one. `project_area_actual` is the operational lookup;
`area_code_evidence` keeps its shape and regains a denormalized project-set
array as old-area metadata (not consulted by the resolution mechanism).

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from dxb.db.alembic_guards import has_column, has_table
from dxb_core.models import Base
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Guarded (dxb.db.alembic_guards): the drop mirrors 0007's guarded add —
    # only drop what is actually there. The add/create below are no-ops on a
    # fresh volume where 0001's create_all() already made the current shape.
    if has_column(bind, "dim_area", "superseded_by_area_id"):
        op.drop_column("dim_area", "superseded_by_area_id")
    if not has_column(bind, "area_code_evidence", "evidence_project_ids"):
        op.add_column(
            "area_code_evidence",
            sa.Column("evidence_project_ids", ARRAY(sa.Integer()), nullable=True),
        )
    if not has_table(bind, "project_area_actual"):
        Base.metadata.tables["project_area_actual"].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["project_area_actual"].drop(bind=bind)
    op.drop_column("area_code_evidence", "evidence_project_ids")
    op.add_column(
        "dim_area",
        sa.Column(
            "superseded_by_area_id",
            sa.Integer(),
            sa.ForeignKey("dim_area.id"),
            nullable=True,
        ),
    )
