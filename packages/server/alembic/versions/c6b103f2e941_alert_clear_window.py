"""记录首次确认风险消失及最近确认时间，避免提前解除预警。"""

import sqlalchemy as sa

from alembic import op

revision = "c6b103f2e941"
down_revision = "c5a092e1d830"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.add_column(sa.Column("clear_since", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("last_clear_check_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.drop_column("last_clear_check_at")
        batch_op.drop_column("clear_since")
