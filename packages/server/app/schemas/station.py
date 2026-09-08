"""站点接口的请求与响应。docs/06 §五"""

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Coord, StationStatus, StationType


class StationMetrics(BaseModel):
    """运行指标。V1 无实测数据，由气象推算；推算前为 None，前端展示「—」。

    字段不带默认值：契约要求缺失一律 null 而非省略（docs/06 §2.5），
    带默认值会让 OpenAPI 把字段标成可选，前端类型就会多一层 undefined。
    """

    daily_generation: float | None = Field(description="kWh")
    current_power: float | None = Field(description="kW")
    total_generation: float | None = Field(description="kWh")
    co2_reduction: float | None = Field(description="kg")

    @classmethod
    def empty(cls) -> "StationMetrics":
        return cls(
            daily_generation=None, current_power=None, total_generation=None, co2_reduction=None
        )


class StationSummary(BaseModel):
    id: str
    name: str
    type: StationType
    status: StationStatus
    capacity: float = Field(description="kW，基础单位裸数值，前端负责进位")
    latitude: float
    longitude: float
    address: str | None
    image: str | None
    metrics: StationMetrics


class StationCounts(BaseModel):
    all: int
    solar: int
    wind: int


class StationListResponse(BaseModel):
    stations: list[StationSummary]
    counts: StationCounts


class CreateStationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: StationType
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    capacity: float = Field(gt=0, description="kW")
    coord: Coord = Field(default=Coord.WGS84, description="入参坐标系")

    # 出力模型参数，选填，不填用默认值。docs/07 §2.3
    tilt: float | None = Field(default=None, ge=0, le=90)
    azimuth: float | None = Field(default=None, ge=0, le=360)
    hub_height: float | None = Field(default=None, gt=0, le=200)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("站点名称不能为空")
        return v


class UpdateStationRequest(BaseModel):
    """PATCH：全部可选。docs/06 §5.4"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    type: StationType | None = None
    status: StationStatus | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    capacity: float | None = Field(default=None, gt=0)
    coord: Coord = Coord.WGS84
    tilt: float | None = Field(default=None, ge=0, le=90)
    azimuth: float | None = Field(default=None, ge=0, le=360)
    hub_height: float | None = Field(default=None, gt=0, le=200)
