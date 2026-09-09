"""公开电站目录：来自公开数据集的场站信息，全体用户共享、只读。docs/04 §七、docs/06 §5.5

所有用户直接浏览目录并以目录 ID 查看气象；历史个人站点通过 catalog_id 溯源，
目录本身不属于任何用户。
"""

from datetime import datetime

from sqlalchemy import JSON, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class CatalogPlant(Base):
    __tablename__ = "catalog_plants"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)  # {source}:{source_id}
    source: Mapped[str] = mapped_column(String(16), index=True)  # wri | gem
    source_id: Mapped[str] = mapped_column(String(64))

    name: Mapped[str] = mapped_column(String(128))  # 数据集原名（多为英文）
    name_local: Mapped[str | None] = mapped_column(String(128), default=None)  # 中文名
    type: Mapped[str] = mapped_column(String(16), index=True)  # solar | wind
    capacity_kw: Mapped[float] = mapped_column(Float)
    # 一律 WGS84。docs/06 §2.2
    latitude: Mapped[float] = mapped_column(Float, index=True)
    longitude: Mapped[float] = mapped_column(Float, index=True)

    province: Mapped[str | None] = mapped_column(String(32), default=None)
    city: Mapped[str | None] = mapped_column(String(32), default=None)
    district: Mapped[str | None] = mapped_column(String(128), default=None)
    owner_name: Mapped[str | None] = mapped_column(String(128), default=None)
    commissioning_year: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str] = mapped_column(String(16), default="operating")

    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    @property
    def display_name(self) -> str:
        return self.name_local or self.name
