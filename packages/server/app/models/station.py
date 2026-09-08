"""站点。字段语义见 docs/04 §六、docs/07 §2.3。"""

import uuid
from datetime import datetime

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)

    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(16))  # solar | wind
    status: Mapped[str] = mapped_column(String(16), default="normal")

    # 一律 WGS84。docs/06 §2.2
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    capacity_kw: Mapped[float] = mapped_column(Float)

    address: Mapped[str | None] = mapped_column(String(128), default=None)
    image: Mapped[str | None] = mapped_column(String(256), default=None)

    # 从公开电站目录添加时记录来源，便于日后同步目录更新
    catalog_id: Mapped[str | None] = mapped_column(String(24), default=None)

    # 出力模型参数，选填。None 时用 docs/07 §2.3 的默认值
    tilt: Mapped[float | None] = mapped_column(Float, default=None)
    azimuth: Mapped[float | None] = mapped_column(Float, default=None)
    hub_height: Mapped[float | None] = mapped_column(Float, default=None)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
