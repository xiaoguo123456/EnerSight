"""扩展公开电站区县字段，兼容 GEM 数据中的完整地名。"""

import sqlalchemy as sa
from alembic import op

revision = "a83d192f4b60"
down_revision = "37c98398bf8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("catalog_plants") as batch_op:
        batch_op.alter_column(
            "district", existing_type=sa.String(32), type_=sa.String(128), existing_nullable=True
        )


def downgrade() -> None:
    # 不截断已有地名；存在超长数据时拒绝缩回，防止静默丢失信息。
    count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM catalog_plants WHERE length(district) > 32")
    ).scalar_one()
    if count:
        raise RuntimeError("目录含超过 32 字符的区县名称，不能安全缩短字段")
    with op.batch_alter_table("catalog_plants") as batch_op:
        batch_op.alter_column(
            "district", existing_type=sa.String(128), type_=sa.String(32), existing_nullable=True
        )
