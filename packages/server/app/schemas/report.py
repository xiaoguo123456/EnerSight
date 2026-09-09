"""AI 报告接口。docs/06 §十"""

from typing import Literal

from pydantic import BaseModel, Field

from app.ai.schema import ReportPeriod
from app.schemas.common import MetricWithDelta
from app.schemas.station import StationSummary


class ReportSummary(BaseModel):
    generation: MetricWithDelta = Field(description="kWh")
    equivalent_hours: MetricWithDelta = Field(description="h")
    co2_reduction: MetricWithDelta = Field(description="kg")
    estimated_revenue: MetricWithDelta = Field(description="元")


class ReportPeriodOut(ReportPeriod):
    time_range: str = Field(description="「06:00 – 12:00」")


class AIReportResponse(BaseModel):
    station: StationSummary
    report_date: str
    generated_at: str
    method: Literal["rule", "ai"] = Field(description="实际生成方式")
    is_fallback: bool = Field(description="供埋点统计，不用于改变展示")

    verdict_title: str
    verdict_detail: str
    periods: list[ReportPeriodOut]
    risk_title: str | None
    risk_detail: str | None
    suggestions: list[str]
    summary: ReportSummary
