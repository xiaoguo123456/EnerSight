"""发电预测：电量与功率分开，覆盖不完整时不宣称全量。docs/16、docs/17 §二、§四"""

from pydantic import BaseModel, Field

from app.schemas.common import IndexLevel


class ForecastBasis(BaseModel):
    """这份预测用的是哪一批气象数据。issued_at 为 null 表示无法确认起报，前端只显示拉取时间。"""

    model: str = Field(description="请求用的模型参数，best_match 即自动选择")
    resolved_model: str | None = Field(description="实际落到的模型（元数据 slug），未能确认为 null")
    issued_at: str | None = Field(description="模型起报时刻，ISO 8601，北京时间偏移")
    available_at: str | None = Field(description="该批数据可用时刻")
    fetched_at: str = Field(description="服务端从上游拉取的时刻")


class PowerPoint(BaseModel):
    time: str
    value: float | None


class ProvinceGrid(BaseModel):
    """限电第二层参考：按省级月度利用率折算。只作参考，不改可发电量与指数。docs/17 §四"""

    region: str = Field(description="利用率统计区域；内蒙古分蒙西、蒙东")
    period: str = Field(description="所用利用率的统计期：YYYY-MM 为当月值，YYYY 为全年值")
    utilization: float = Field(description="该类型新能源利用率，0–1")
    energy_kwh: float = Field(description="可发电量 × 利用率")
    curtailed_kwh: float = Field(description="可发电量 × (1 − 利用率)")
    source: str


class FleetProvinceGrid(BaseModel):
    """全目录逐日合计的省级限电参考。docs/17 §四"""

    energy_kwh: float = Field(
        description="已覆盖电站按各自省级利用率折算后的合计；无省级数据的电站按可发电量计入"
    )
    curtailed_kwh: float
    applied_count: int = Field(description="有省级利用率、参与折算的电站数")
    unapplied_count: int = Field(description="无省级利用率（省份或城市不详）、未折算的电站数")
    periods: list[str] = Field(description="用到的利用率统计期")
    source: str


class GenerationPrediction(BaseModel):
    calculation_version: str | None = None
    estimated: bool = False
    model: str
    date: str
    timezone: str = "Asia/Shanghai"
    generated_at: str
    energy_kwh: float | None = Field(description="可发电量，气象潜在值")
    power_kw: list[PowerPoint]
    # 计入场站出力约束后的口径；没有规则一律 null。docs/17 §四
    grid_energy_kwh: float | None = None
    curtailed_kwh: float | None = None
    grid_power_kw: list[PowerPoint] | None = None
    # 限电第二层：公开电站按省级月度利用率折算的参考；自建场站与无数据时为 null
    province_grid: ProvinceGrid | None = None
    basis: ForecastBasis | None = None
    resolution_minutes: int = Field(default=60, description="曲线间隔：15 为 96 点，60 为 24 点")
    assumptions: list[str] = Field(default_factory=list)


class DailyOutlook(BaseModel):
    """7 天预测里的一天。指数只反映气象，不受出力约束影响。"""

    date: str
    estimated: bool = False
    weekday: int = Field(description="ISO 1–7")
    energy_kwh: float | None
    grid_energy_kwh: float | None
    curtailed_kwh: float | None
    province_grid: ProvinceGrid | None = Field(
        description="公开电站按省级月度利用率折算的上网参考；自建场站与无数据时为 null"
    )
    index_score: float | None
    index_level: IndexLevel | None
    weather_text: str | None = Field(description="日间众数天气")
    peak_kw: float | None
    power_kw: list[PowerPoint]
    grid_power_kw: list[PowerPoint] | None
    lead_days: int = Field(description="距今天数，0 为今日；1–3 为短期（日前）；≥4 为中期")
    resolution_minutes: int = Field(description="当前点预报统一 15 分钟；历史小时资料保留 60 分钟")


class StationOutlook(BaseModel):
    calculation_version: str | None = None
    station_id: str
    model: str
    timezone: str
    generated_at: str
    basis: ForecastBasis | None
    days: list[DailyOutlook]
    assumptions: list[str] = Field(default_factory=list)


class RegionPrediction(BaseModel):
    province: str
    energy_kwh: float
    covered_count: int


class FleetDay(BaseModel):
    resolution_minutes: int = Field(default=60, description="曲线间隔分钟数，兼容旧留档")
    date: str
    weekday: int
    lead_days: int
    energy_kwh: float | None
    solar_kwh: float
    wind_kwh: float
    power_kw: list[PowerPoint]
    covered_count: int = 0
    covered_capacity_kw: float = 0
    failed_count: int = 0
    status: str = "queued"
    common_energy_kwh: float | None = None
    regions: list[RegionPrediction] = Field(default_factory=list)
    # 旧留档没有该字段，保留默认值以便续用昨日快照时能读回
    province_grid: FleetProvinceGrid | None = None


class FleetPrediction(GenerationPrediction):
    updating: bool = Field(default=False, description="后台检查或计算中；已完成快照继续可用")
    input_archive_id: str | None = Field(default=None, description="不可变气象与目录输入留档标识")
    batch_stamp: str | None = None
    catalog_revision: str | None = None
    common_covered_count: int = 0
    common_capacity_kw: float = 0
    status: str
    total_count: int = 0
    eligible_count: int = 0
    covered_count: int = 0
    duplicate_count: int = 0
    invalid_count: int = 0
    failed_count: int = 0
    total_capacity_kw: float = 0
    covered_capacity_kw: float = 0
    solar_kwh: float = 0
    wind_kwh: float = 0
    regions: list[RegionPrediction] = Field(default_factory=list)
    # 未来 7 天，days[0] 与顶层今日字段一致。docs/17 §二
    days: list[FleetDay] = Field(default_factory=list)
    message: str | None = None
