"""保留目录分期与容量来源，不改写既有场站 ID。"""

import sqlalchemy as sa
from alembic import op

revision = "c291b8d4f230"
down_revision = "b72e69410cfa"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("catalog_plants", sa.Column("provenance", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("catalog_plants", "provenance")
