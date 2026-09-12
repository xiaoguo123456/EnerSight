"""自建站点出力约束与逐日限电损失。docs/17 §四"""

import sqlalchemy as sa

from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "c6b103f2e941"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stations") as batch_op:
        batch_op.add_column(sa.Column("curtailment", sa.JSON(), nullable=True))
    with op.batch_alter_table("daily_generation") as batch_op:
        batch_op.add_column(sa.Column("curtailed_kwh", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("daily_generation") as batch_op:
        batch_op.drop_column("curtailed_kwh")
    with op.batch_alter_table("stations") as batch_op:
        batch_op.drop_column("curtailment")
