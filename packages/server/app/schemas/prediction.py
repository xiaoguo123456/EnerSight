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
