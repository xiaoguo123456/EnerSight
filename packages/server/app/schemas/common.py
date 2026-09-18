"""公共类型。对应 docs/06 §十二。

约定：
- 数据缺失一律 None，不省略字段、不用 0 代替（docs/06 §2.5）
- 数值为基础单位裸值，不带单位、不进位（docs/06 §2.3）
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class Coord(StrEnum):
    WGS84 = "wgs84"
    GCJ02 = "gcj02"


class StationType(StrEnum):
    SOLAR = "solar"
    WIND = "wind"


class StationStatus(StrEnum):
    NORMAL = "normal"
    STANDBY = "standby"
    FAULT = "fault"


class LayerType(StrEnum):
    CLOUD = "cloud"
    WIND = "wind"
    TEMPERATURE = "temperature"
    RADIATION = "radiation"


class IndexLevel(StrEnum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


class AlertLevel(StrEnum):
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    CLEARED = "cleared"


class SpreadLevel(StrEnum):
    """三模式对同一天的分歧程度。说的是预报有多稳，不是天气好坏。docs/19 §一"""

    AGREE = "agree"
    DIVERGE = "diverge"
    STRONG = "strong"


class ConvergenceLevel(StrEnum):
    """同一模型历次起报的摆动程度。docs/19 §二"""

    STABLE = "stable"
    WOBBLE = "wobble"
    SWING = "swing"


class MetricWithDelta(BaseModel):
    """值 + 环比。delta_percent 为 None 时前端隐藏环比标签。"""

    value: float | None
    delta_percent: float | None = Field(description="较昨日同期百分比，None 时前端隐藏标签")


class IndexAttributionFactor(StrEnum):
    RADIATION = "radiation"
    TEMPERATURE = "temperature"
    WIND = "wind"


class IndexAttribution(BaseModel):
    """归因：该因子使指数偏离理想值多少分。算出来的，不是权重。docs/07 §1.6"""

    factor: IndexAttributionFactor
    delta: float = Field(description="负数为扣分，正数为加分")
    description: str


class EnergyIndex(BaseModel):
    """环境指数 = 今日预测发电量 / 今日理想发电量 × 100。docs/07 §一"""

    score: float | None = None
    level: IndexLevel | None = None
    summary: str | None = Field(default=None, description="AI 一句话结论")
    estimated: bool = Field(default=False, description="True 表示部分气象因子由气候平均值填补")
    attribution: list[IndexAttribution] = Field(default_factory=list)
