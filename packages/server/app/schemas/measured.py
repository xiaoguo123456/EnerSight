"""实测电量与订正。docs/19 §三"""

from typing import Literal

from pydantic import BaseModel, Field

MeasuredKind = Literal["day", "month"]
MeasuredBasis = Literal["generation", "grid"]
CorrectionMethod = Literal["day", "month"]
EntryStatus = Literal["used", "excluded", "pending", "idle"]


class MeasuredEntryIn(BaseModel):
    kind: MeasuredKind
    date: str = Field(
        pattern=r"^\d{4}-\d{2}(-\d{2})?$",
        description="日电量 YYYY-MM-DD，月电量 YYYY-MM",
    )
    kwh: float = Field(ge=0, description="kWh，基础单位")


class RecordMeasuredRequest(BaseModel):
    entries: list[MeasuredEntryIn] = Field(min_length=1, max_length=31)
    basis: MeasuredBasis = Field(
        default="generation", description="发电量或上网电量；上网电量会把线损与厂用电一起算进系数"
    )


class MeasuredEntry(BaseModel):
    id: int
    kind: MeasuredKind
    date: str = Field(description="日电量 YYYY-MM-DD，月电量 YYYY-MM")
    kwh: float
    basis: MeasuredBasis
    model_kwh: float | None = Field(description="模型同期电量（未订正）；还没回算出来为 null")
    ratio: float | None = Field(description="实测 / 模型")
    status: EntryStatus = Field(
        description="used 进了本次拟合；excluded 比值出界、判为停机或录错；"
        "pending 模型值还没算出来；idle 不在拟合窗口或另一档正在用"
    )
    recorded_at: str


class CorrectionStatus(BaseModel):
    enabled: bool = Field(description="用户开关")
    applied: bool = Field(description="开关打开且拟合通过，正在作用于预测")
    k: float | None = Field(description="实测 / 模型；样本不够时为 null")
    method: CorrectionMethod | None
    sample_count: int
    excluded_count: int
    error_before: float | None = Field(description="逐条平均误差（%）：|模型 / 实测 − 1| 的平均")
    error_after: float | None = Field(
        description="留一法订正后的逐条平均误差（%）：每条用其余记录的系数修正"
    )
    fitted_at: str | None
    reason: str | None = Field(description="未生效的原因，或还差几条记录")


class MeasuredSummary(BaseModel):
    station_id: str
    entries: list[MeasuredEntry] = Field(description="按日期倒序")
    correction: CorrectionStatus


class CorrectionApplied(BaseModel):
    """正在作用于预测的订正。随预测一起返回，给徽章与 ⓘ 用。"""

    k: float
    method: CorrectionMethod
    sample_count: int
    error_before: float | None
    error_after: float | None
    fitted_at: str
