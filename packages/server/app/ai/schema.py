"""AI 报告的结构化输出。docs/08 §四

同一个 Pydantic 模型既约束模型输出，又参与 OpenAPI 生成。
数据摘要（发电量 / 等效小时 / CO₂ / 收益）不进这里 —— 那是算出来的，
由服务端直接填充，不经过模型。
"""

from typing import Literal

from pydantic import BaseModel, Field


class ReportPeriod(BaseModel):
    period: Literal["morning", "afternoon", "evening"]
    weather_summary: str = Field(description="「晴转多云」")
    generation_impact: str = Field(description="「发电条件良好」")
    level: Literal["good", "warning", "risk"] = Field(description="时间轴节点配色")


class AIReport(BaseModel):
    verdict_title: str = Field(description="「今日适宜发电，下午存在轻度云层风险」")
    verdict_detail: str = Field(description="整体建议说明")
    periods: list[ReportPeriod] = Field(min_length=3, max_length=3)
    risk_title: str | None = Field(description="无风险时为 null，不要编造")
    risk_detail: str | None
    suggestions: list[str] = Field(min_length=2, max_length=3)
