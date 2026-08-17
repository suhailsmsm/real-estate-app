"""dim_building + building_id on facts + project building-geo columns

Adds the building layer (docs/PROJECT_GEO_ENRICHMENT.md §6): a `dim_building`
carrying a Makani-geocoded `location` plus physical attributes enriched from the
data.dubai building CSVs, a `building_id` FK on both fact tables, and two
building-derived confidence columns on `dim_project` (`geo_building_count`,
`geo_spread_m`). Also seeds the `makani` and `datadubai_buildings` sources.

The new table is created from its model definition (like 0001) so the column
list stays in one place; the ALTERs are explicit.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from dxb.db.alembic_guards import has_column, has_table
from dxb_core.models import Base

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Guarded (dxb.db.alembic_guards): on a fresh volume 0001's create_all()
    # already created everything at the current model shape, so each additive
    # op below is a no-op there — and still runs on an incremental volume.
    if not has_table(bind, "dim_building"):
        # New table straight from the model (keeps its columns in one place).
        Base.metadata.tables["dim_building"].create(bind=bind)

    for table in ("fact_sale_transaction", "fact_rent_contract"):
        if not has_column(bind, table, "building_id"):
            op.add_column(
                table,
                sa.Column(
                    "building_id",
                    sa.BigInteger(),
                    sa.ForeignKey("dim_building.id"),
                    nullable=True,
                ),
            )
    if not has_column(bind, "dim_project", "geo_building_count"):
        op.add_column(
            "dim_project", sa.Column("geo_building_count", sa.Integer(), nullable=True)
        )
    if not has_column(bind, "dim_project", "geo_spread_m"):
        op.add_column(
            "dim_project", sa.Column("geo_spread_m", sa.Numeric(10, 1), nullable=True)
        )

    # Seed the two new sources (idempotent — skips any that already exist).
    from sqlalchemy.orm import Session

    from dxb.db.engine import seed_sources

    seed_sources(Session(bind=bind))


def downgrade() -> None:
    op.drop_column("dim_project", "geo_spread_m")
    op.drop_column("dim_project", "geo_building_count")
    op.drop_column("fact_rent_contract", "building_id")
    op.drop_column("fact_sale_transaction", "building_id")
    op.drop_table("dim_building")
