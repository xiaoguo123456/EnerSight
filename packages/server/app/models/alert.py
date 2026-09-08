"""预警记录。规则见 docs/07 §五，接口见 docs/06 §九。"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    station_id: Mapped[str] = mapped_column(String(12), index=True)

    # 类型：cloud 云层/辐射下降 | wind 强风 | rain 暴雨 | heat 高温 | cold 低温
    kind: Mapped[str] = mapped_column(String(16))
    # minor | moderate | severe | cleared
    level: Mapped[str] = mapped_column(String(16))
    # forecast 气象预报 | satellite 卫星短临
    source: Mapped[str] = mapped_column(String(16), default="forecast")

    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)

    # 该预警是否仍在生效；解除时置 False 并生成一条 cleared 记录
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # naive UTC，出口按站点时区格式化
    published_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
