"""自建站点的机型、功率曲线与光伏安装方式。docs/07 §2.3"""

import sqlalchemy as sa

from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stations") as batch_op:
        batch_op.add_column(sa.Column("turbine_class", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("power_curve", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("mounting", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("bifacial", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("stations") as batch_op:
        batch_op.drop_column("bifacial")
        batch_op.drop_column("mounting")
        batch_op.drop_column("power_curve")
        batch_op.drop_column("turbine_class")
