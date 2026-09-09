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


class CatalogPhase(BaseModel):
    id: str
    name: str
    phase_name: str
    capacity_kw: float
    capacity_rating: str
    owner: str = ""


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
    source: str | None = None
    original_name: str | None = None
    local_name: str | None = None
    catalog_updated_at: str | None = Field(
        default=None, description="目录入库更新时间，不代表数据源发布日"
    )
    owner_name: str | None = None
    phases: list[CatalogPhase] = Field(default_factory=list)
    source_file: str | None = None
    capacity_note: str | None = None
    prediction_blocked_reason: str | None = None


class StationCounts(BaseModel):
    all: int
    solar: int
    wind: int


class StationListResponse(BaseModel):
    stations: list[StationSummary]
    counts: StationCounts


class PublicStationListResponse(StationListResponse):
    total: int = Field(description="当前搜索和类型筛选的总数")
    has_more: bool
    regions: list[str] = Field(description="目录中可筛选的省级地区")


class CreateStationRequest(BaseModel):
    """两种建法：给 catalog_id 从公开电站目录复制（其余字段可省，给了则覆盖）；
    或者不给 catalog_id、把五个必填字段都给全（API 保留，小程序 V1 不提供自建入口）。"""

    catalog_id: str | None = Field(default=None, max_length=24)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    type: StationType | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    capacity: float | None = Field(default=None, gt=0, description="kW")
    coord: Coord = Field(default=Coord.WGS84, description="入参坐标系")

    # 出力模型参数，选填，不填用默认值。docs/07 §2.3
    tilt: float | None = Field(default=None, ge=0, le=90)
    azimuth: float | None = Field(default=None, ge=0, le=360)
    hub_height: float | None = Field(default=None, gt=0, le=200)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
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
