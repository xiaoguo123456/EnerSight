"""公开目录数据质量：占位坐标近似修正、WRI 重复标记、缺省份补全、合并场址命名。

逻辑在 app/catalog/quality.py，只依赖库内字段，不需要原始数据文件；导入与同步之后也会再跑。
降级按溯源里保存的原坐标、原名称与状态恢复。docs/04 §七
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "7c1e4d2b9a53"
down_revision = "a713d91e2001"
branch_labels = None
depends_on = None


def upgrade():
    from app.catalog import quality
    from app.models import CatalogPlant

    with Session(bind=op.get_bind()) as session:
        report = quality.apply(session.scalars(sa.select(CatalogPlant)).all())
        session.flush()
    print(f"catalog quality: {report}")


def downgrade():
    from app.catalog import quality
    from app.models import CatalogPlant

    with Session(bind=op.get_bind()) as session:
        for p in session.scalars(sa.select(CatalogPlant)).all():
            prov = dict(p.provenance or {})
            location = prov.pop("location", None)
            if location:
                p.latitude, p.longitude = location["original"]
            if prov.pop("duplicate", None) is not None and p.status == quality.DUPLICATE:
                p.status = "operating"
            if "name_local_source" in prov:
                p.name_local = prov.pop("name_local_source")
            if prov.pop("region_from", None) is not None:
                p.province = p.city = None
            if prov != (p.provenance or {}):
                p.provenance = prov or None
        session.flush()
