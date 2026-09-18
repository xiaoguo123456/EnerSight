"""站点。字段语义见 docs/04 §六、docs/07 §2.3。"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, String, true
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

    # 场站级出力约束（限电 / 检修），只有自建站点有。结构见 schemas.station.CurtailmentRule，
    # 应用见 services/curtailment。docs/17 §四
    curtailment: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)

    # 机型与安装方式，自建站点选填。docs/07 §2.1–2.3
    turbine_class: Mapped[str | None] = mapped_column(String(16), default=None)
    power_curve: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    mounting: Mapped[str | None] = mapped_column(String(16), default=None)
    bifacial: Mapped[bool | None] = mapped_column(Boolean, default=None)

    # 实测订正开关：拟合满足条件时是否作用于预测。默认开。docs/19 §三
    correction_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
