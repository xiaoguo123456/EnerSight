"""实测电量与订正系数。docs/19 §三

实测是用户经营数据：只属于这座自建电站，随删除电站或「删除我的数据」一并删除。
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class MeasuredEnergy(Base):
    """用户记下的一天或一个月的电量。"""

    __tablename__ = "measured_energy"
    __table_args__ = (
        UniqueConstraint("station_id", "period_start", "period_end", name="uq_measured_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(String(24), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # day | month
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    kwh: Mapped[float] = mapped_column(Float)
    basis: Mapped[str] = mapped_column(String(16), default="generation")  # generation | grid
    # 模型同期电量（past_days 回算，未订正）与算它时的参数指纹；指纹变了就重算
    model_kwh: Mapped[float | None] = mapped_column(Float, default=None)
    model_digest: Mapped[str | None] = mapped_column(String(32), default=None)
    # 日电量写进逐日累积表时覆盖掉的推算值；删除这条记录时恢复。None = 原来没有那一行
    prior_kwh: Mapped[float | None] = mapped_column(Float, default=None)
    recorded_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class StationCorrection(Base):
    """每站最近一次拟合。applied 为假时系数只保存、不作用于预测。"""

    __tablename__ = "station_correction"

    station_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    fitted_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    method: Mapped[str | None] = mapped_column(String(8), default=None)  # day | month
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0)
    k: Mapped[float | None] = mapped_column(Float, default=None)
    error_before: Mapped[float | None] = mapped_column(Float, default=None)
    error_after: Mapped[float | None] = mapped_column(Float, default=None)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(128), default=None)
