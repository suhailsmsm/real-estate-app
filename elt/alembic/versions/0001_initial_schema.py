"""initial star schema — single squashed baseline

Builds the complete current schema from the models in one step. Squashed
2026-07-22 during the data.dubai rebuild: the earlier incremental migrations
(is_government / nullable is_freehold / OSM geo columns) were folded back into
the models, because this migration uses create_all() and re-running those
ALTERs on a from-zero volume would collide with the columns create_all had
already produced. See docs/DATADUBAI_REBUILD_PLAN.md §2.

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""

from dxb_core.models import Base

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
