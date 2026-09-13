"""逐日记录保存当地时区、模型与计算版本。"""

import sqlalchemy as sa
from alembic import op

revision = "a713d91e2001"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "daily_generation",
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
    )
    op.add_column("daily_generation", sa.Column("calculation_version", sa.String(128)))
    op.add_column("daily_generation", sa.Column("weather_model", sa.String(32)))


def downgrade():
    op.drop_column("daily_generation", "weather_model")
    op.drop_column("daily_generation", "calculation_version")
    op.drop_column("daily_generation", "timezone")
