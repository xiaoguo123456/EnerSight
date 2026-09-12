"""首页聚合与趋势。docs/06 §六、§八、§十二

可空字段一律不带默认值：契约要求缺失一律 null 而非省略。
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.common import AlertLevel, EnergyIndex, MetricWithDelta
from app.schemas.prediction import ForecastBasis, GenerationPrediction
from app.schemas.station import StationSummary


class TrendMetric(StrEnum):
    RADIATION = "radiation"
    WIND_SPEED = "wind_speed"
    CLOUD_COVER = "cloud_cover"


class TrendRange(StrEnum):
    H24 = "24h"
    D7 = "7d"


class TrendPoint(BaseModel):
    time: str
    value: float | None = Field(description="缺测为 null，前端断线不补 0")


class TrendSeries(BaseModel):
    metric: TrendMetric
    unit: str
    range: TrendRange
    y_max: float | None = Field(description="固定纵轴上限；null 表示自适应。由服务端下发")
    points: list[TrendPoint]


class CurrentWeather(BaseModel):
    temperature: MetricWithDelta = Field(description="℃")
    apparent_temperature: float | None = Field(description="℃ 体感")
    humidity: float | None = Field(description="%")
    wind_speed: MetricWithDelta = Field(description="m/s")
    wind_direction: float | None = Field(description="°")
    cloud_cover: MetricWithDelta = Field(description="%")
    radiation: MetricWithDelta = Field(description="W/m²")
    weather_text: str | None = Field(description="「晴转多云」")
    observed_at: str


class AlertSummary(BaseModel):
    id: str
    level: AlertLevel
    title: str
    description: str
    published_at: str
    station_id: str
    source: str


class HomeResponse(BaseModel):
    prediction: GenerationPrediction | None = None
    has_station: bool
    station: StationSummary | None
    index: EnergyIndex | None
    weather: CurrentWeather | None
    trends: TrendSeries | None = Field(description="24 小时，默认辐射")
    alert: AlertSummary | None = Field(description="今日最高等级一条，无预警为 null")


class StationDetailResponse(BaseModel):
    """docs/06 §5.3"""

    station: StationSummary
    weather: CurrentWeather | None
    index: EnergyIndex | None
    trends: TrendSeries | None
    updated_at: str = Field(description="「数据更新时间」，取气象观测时刻")
    basis: ForecastBasis | None = Field(description="气象批次：模型、起报、拉取时刻")


class MapOverviewResponse(BaseModel):
    """docs/06 §7.1"""

    station: StationSummary
    index: EnergyIndex | None
    weather: CurrentWeather | None
    ai_hint: str | None = Field(description="底部 AI 提示条一句话")
