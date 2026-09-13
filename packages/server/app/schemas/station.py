"""站点接口的请求与响应。docs/06 §五"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import Coord, StationStatus, StationType


class CurtailmentWindow(BaseModel):
    """分时段出力上限。end_hour 不含；start >= end 表示跨零点。weekdays 为 ISO 1–7，空为每天。"""

    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=24)
    limit_percent: float = Field(ge=0, le=100, description="时段内出力上限，装机容量的百分比")
    weekdays: list[int] = Field(default_factory=list)

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v: list[int]) -> list[int]:
        out = sorted(set(v))
        if any(d < 1 or d > 7 for d in out):
            raise ValueError("weekdays 取 1–7（周一到周日）")
        return out


class CurtailmentRule(BaseModel):
    """场站级出力约束（限电 / 检修），两种写法二选一。docs/17 §四"""

    mode: Literal["ratio", "schedule"]
    ratio_percent: float | None = Field(default=None, gt=0, le=100, description="固定限电比例")
    windows: list[CurtailmentWindow] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _shape(self) -> "CurtailmentRule":
        if self.mode == "ratio" and self.ratio_percent is None:
            raise ValueError("ratio 模式需要 ratio_percent")
        if self.mode == "schedule" and not self.windows:
            raise ValueError("schedule 模式至少一条时段")
        return self


TurbineClass = Literal["generic", "low_wind", "medium_wind", "high_wind", "custom"]
Mounting = Literal["fixed", "single_axis"]


class PowerCurvePoint(BaseModel):
    v: float = Field(ge=0, le=60, description="轮毂高度风速 m/s")
    p: float = Field(ge=0, le=100, description="出力占额定容量百分比")


def _check_curve(curve: list[PowerCurvePoint] | None) -> list[PowerCurvePoint] | None:
    if curve is None:
        return None
    if len(curve) < 3:
        raise ValueError("功率曲线至少 3 个点")
    for a, b in zip(curve, curve[1:], strict=False):
        if b.v <= a.v:
            raise ValueError("功率曲线的风速必须递增")
    return curve


class StationMetrics(BaseModel):
    """运行指标。V1 无实测数据，由气象推算；推算前为 None，前端展示「—」。

    字段不带默认值：契约要求缺失一律 null 而非省略（docs/06 §2.5），
    带默认值会让 OpenAPI 把字段标成可选，前端类型就会多一层 undefined。
    """

    daily_generation: float | None = Field(description="kWh")
    current_power: float | None = Field(description="kW")
    total_generation: float | None = Field(description="kWh")
    co2_reduction: float | None = Field(description="kg")
    grid_generation: float | None = Field(
        description="kWh 今日预计上网，计入出力约束；无规则为 null。docs/17 §四"
    )

    @classmethod
    def empty(cls) -> "StationMetrics":
        return cls(
            daily_generation=None,
            current_power=None,
            total_generation=None,
            co2_reduction=None,
            grid_generation=None,
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
    tilt: float | None = None
    azimuth: float | None = None
    hub_height: float | None = None
    latitude: float
    longitude: float
    address: str | None
    image: str | None
    metrics: StationMetrics
    is_own: bool = Field(description="True 为本账号自建站点，可编辑删除；目录电站为 False")
    curtailment: CurtailmentRule | None = Field(description="出力约束，目录电站与未设置时为 null")
    turbine_class: TurbineClass | None = Field(description="风电机型档，未设置为 null 即通用曲线")
    power_curve: list[PowerCurvePoint] | None = Field(description="自定义功率曲线，仅 custom 档")
    mounting: Mounting | None = Field(description="光伏安装方式，未设置为 null 即固定支架")
    bifacial: bool | None = Field(description="光伏是否双面组件，未设置为 null 即单面")
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
    或者不给 catalog_id、把五个必填字段都给全（自建，docs/17 §一）。"""

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
    curtailment: CurtailmentRule | None = None
    # 机型与安装方式，docs/07 §2.3
    turbine_class: TurbineClass | None = None
    power_curve: list[PowerCurvePoint] | None = Field(default=None, max_length=60)
    mounting: Mounting | None = None
    bifacial: bool | None = None

    @field_validator("power_curve")
    @classmethod
    def _curve(cls, v: list[PowerCurvePoint] | None) -> list[PowerCurvePoint] | None:
        return _check_curve(v)

    @model_validator(mode="after")
    def _custom_needs_curve(self) -> "CreateStationRequest":
        if self.turbine_class == "custom" and not self.power_curve:
            raise ValueError("自定义机型需要功率曲线")
        return self

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
    # 传 null 清除规则；不传保持不变
    curtailment: CurtailmentRule | None = None
    turbine_class: TurbineClass | None = None
    power_curve: list[PowerCurvePoint] | None = Field(default=None, max_length=60)
    mounting: Mounting | None = None
    bifacial: bool | None = None

    @field_validator("power_curve")
    @classmethod
    def _curve(cls, v: list[PowerCurvePoint] | None) -> list[PowerCurvePoint] | None:
        return _check_curve(v)
