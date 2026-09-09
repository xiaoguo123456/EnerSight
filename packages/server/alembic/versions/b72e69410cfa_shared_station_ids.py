"""预警、报告与发电记录支持公开电站 ID。"""

import sqlalchemy as sa

from alembic import op

revision = "b72e69410cfa"
down_revision = "a83d192f4b60"
branch_labels = None
depends_on = None
TABLES = ("alerts", "reports", "daily_generation")


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "station_id",
                existing_type=sa.String(12),
                type_=sa.String(24),
                existing_nullable=False,
            )


def downgrade() -> None:
    for table in TABLES:
        count = (
            op.get_bind()
            .execute(sa.text(f"SELECT count(*) FROM {table} WHERE length(station_id) > 12"))
            .scalar_one()
        )
        if count:
            raise RuntimeError("已存在公开电站记录，不能安全缩短站点 ID")
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "station_id",
                existing_type=sa.String(24),
                type_=sa.String(12),
                existing_nullable=False,
            )
