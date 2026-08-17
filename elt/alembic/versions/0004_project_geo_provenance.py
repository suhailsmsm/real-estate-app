"""dim_project geo provenance: geo_source_id + geo_match_method

Mirrors dim_area. Lets a project's `location` carry which source placed it and
how — critically distinguishing a `nominatim_validated` point (real, confirmed
inside the project's area) from an `area_centroid` fallback (coarse, shared by
every project in the area). The 10% Nominatim hit rate measured during the
probe (docs/PROJECT_GEO_ENRICHMENT.md) is why this distinction has to be
first-class: most projects will carry the coarse fallback, and the map must be
able to tell them apart.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-24
"""

import sqlalchemy as sa

from alembic import op
from dxb.db.alembic_guards import has_column

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Guarded: on a fresh volume 0001's create_all() creates dim_project at
    # the CURRENT model shape, which already includes these columns — skip
    # them there; still run on an incrementally-migrated volume.
    if not has_column(bind, "dim_project", "geo_source_id"):
        op.add_column(
            "dim_project",
            sa.Column(
                "geo_source_id",
                sa.Integer(),
                sa.ForeignKey("dim_source.id"),
                nullable=True,
            ),
        )
    if not has_column(bind, "dim_project", "geo_match_method"):
        op.add_column(
            "dim_project",
            sa.Column("geo_match_method", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("dim_project", "geo_match_method")
    op.drop_column("dim_project", "geo_source_id")
