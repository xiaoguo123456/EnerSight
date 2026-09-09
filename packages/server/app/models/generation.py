"""逐日发电记录。

V1 无实测，每日由气象推算并累积；接入实测后同一张表存实测值。
累计发电 / 减排量从这里求和。docs/07 §3.1
"""

from datetime import date, datetime

from sqlalchemy import Date, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class DailyGeneration(Base):
    __tablename__ = "daily_generation"
    __table_args__ = (UniqueConstraint("station_id", "day", name="uq_generation_station_day"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(String(24), index=True)
    day: Mapped[date] = mapped_column(Date)
    kwh: Mapped[float] = mapped_column(Float)
    # 最近一次任务运行时的即时功率（kW），列表页展示用；日终后无意义
    current_kw: Mapped[float | None] = mapped_column(Float, default=None)
    # 来源：forecast 推算 / measured 实测。实测优先
    source: Mapped[str] = mapped_column(String(16), default="forecast")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
