"""AI 报告存档。每站每日一份，预生成 + 缓存，用户请求只读。docs/08 §三"""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("station_id", "day", name="uq_report_station_day"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(String(12), index=True)
    day: Mapped[date] = mapped_column(Date)

    # AIReport 的 JSON；数据摘要另存，因为它是算出来的
    content: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(16))
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    # 留档：输入原文，供人工复核与幻觉排查。docs/08 §八
    prompt_input: Mapped[str] = mapped_column(String(4000))

    generated_at: Mapped[datetime] = mapped_column(default=utcnow)
