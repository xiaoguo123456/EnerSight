"""预警加 last_detected_at：解除前要求条件消失并持续 30 分钟稳定（docs/07 §5.3）。"""

import sqlalchemy as sa
from alembic import op

revision = "c4f1a2b7d9e0"
down_revision = "b72e69410cfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.add_column(sa.Column("last_detected_at", sa.DateTime(), nullable=True))
    # 已有的生效中预警：视为刚检测到，避免升级后第一轮扫描就把它们全部解除
    op.execute(sa.text("UPDATE alerts SET last_detected_at = updated_at WHERE active"))


def downgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.drop_column("last_detected_at")
