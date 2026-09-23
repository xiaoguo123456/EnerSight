"""发电预测：电量与功率分开，覆盖不完整时不宣称全量。docs/16、docs/17 §二、§四"""

from pydantic import BaseModel, Field

from app.schemas.common import ConvergenceLevel, IndexLevel, SpreadLevel
from app.schemas.measured import CorrectionApplied


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
    # 按实测订正过；全目录快照要能读回旧文件，所以和 estimated 一样带默认值。docs/19 §三
    corrected: bool = False
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


class MemberEnergy(BaseModel):
    """三模式里某一家对这一天的日电量。拿不到为 null，不省略这一家。docs/19 §一"""

    model: str
    energy_kwh: float | None


class EnsembleSummary(BaseModel):
    """三模式区间的成员与总体分歧度。单一模式请求时整个对象为 null。docs/19 §一"""

    members: list[ForecastBasis] = Field(description="参与的成员及各自起报，按配置顺序")
    spread_level: SpreadLevel | None = Field(description="按首日的日电量分歧度分档")


class DailyOutlook(BaseModel):
    """7 天预测里的一天。指数只反映气象，不受出力约束影响。

    三模式下整套主字段（电量、曲线、指数、天气、峰值）取自**同一个成员** —— 日电量居中的那家，
    逐日独立选，记在 median_model。不做逐点中位合成：那条曲线求和不等于任何一家的日电量，
    主数字与曲线会对不上，峰谷也被削平。docs/19 §一
    """

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
    # 以下为三模式区间；单一模式请求时一律 null。docs/19 §一
    energy_kwh_low: float | None = Field(description="三家日电量的最小值")
    energy_kwh_high: float | None = Field(description="三家日电量的最大值")
    member_energy_kwh: list[MemberEnergy] | None = Field(description="三家各自的日电量")
    median_model: str | None = Field(description="这一天的主数字取自哪个成员")
    power_kw_low: list[PowerPoint] | None = Field(
        description="逐时刻三家最小值，只作视觉包络，不参与求和"
    )
    power_kw_high: list[PowerPoint] | None = Field(description="逐时刻三家最大值，同上")
    spread_percent: float | None = Field(description="(max−min)/median × 100")
    spread_level: SpreadLevel | None
    corrected: bool = Field(description="电量与曲线按实测订正过；指数不受影响。docs/19 §三")


class StationOutlook(BaseModel):
    calculation_version: str | None = None
    station_id: str
    model: str
    timezone: str
    generated_at: str
    basis: ForecastBasis | None
    days: list[DailyOutlook]
    ensemble: EnsembleSummary | None = Field(description="三模式区间；单一模式请求为 null")
    correction: CorrectionApplied | None = Field(description="正在作用的实测订正；没有为 null")
    assumptions: list[str] = Field(default_factory=list)


class Issuance(BaseModel):
    """同一目标日的一次起报。docs/19 §二"""

    issued_at: str | None = Field(description="模型起报时刻；拿不到时为 null，按拉取时间归并")
    generated_at: str
    lead_days: int = Field(description="该次起报距目标日的天数")
    model: str
    energy_kwh: float | None
    peak_kw: float | None


class ForecastEvolution(BaseModel):
    """同一目标日历次起报的变化。只读留档，不触发计算。docs/19 §二"""

    station_id: str
    date: str
    model: str = Field(description="固定单一模型，混模型比的是模型间差异而不是演变")
    issuances: list[Issuance] = Field(description="按起报时刻升序")
    convergence: ConvergenceLevel | None = Field(description="不足 2 份起报时为 null")
    range_percent: float | None = Field(description="最近几份起报的 (max−min)/mean × 100")


class RegionPrediction(BaseModel):
    """按电站所在省汇总。省份不详归入「地区待补充」，是合法汇总项但不作为筛选项。

    拆分与容量三项 2026-09-18 才加，早于此的历史留档没有，读回时为 null，
    分省历史里那几天只画总量不画光伏/风电堆叠。
    """

    province: str
    energy_kwh: float
    covered_count: int
    solar_kwh: float | None = None
    wind_kwh: float | None = None
    covered_capacity_kw: float | None = None


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


class FleetSignalPair(BaseModel):
    current_kwh: float
    previous_kwh: float
    change_percent: float | None = Field(description="上轮为零时为 null")


class FleetSignalHour(BaseModel):
    hour: str = Field(description="北京时间整小时，ISO 8601")
    current_kw: float
    previous_kw: float
    change_capacity_percent: float = Field(description="功率变化占相同样本装机容量的百分点")


class FleetSignalWindow(BaseModel):
    start_hour: str
    end_hour: str
    change_capacity_percent: float


class FleetRegionSignal(BaseModel):
    province: str
    solar: FleetSignalPair | None
    wind: FleetSignalPair | None
    combined: FleetSignalPair | None


class FleetSignalIssuance(BaseModel):
    generated_at: str
    issued_at: str | None
    solar_kwh: float
    wind_kwh: float
    energy_kwh: float


class FleetSignalModel(BaseModel):
    model: str
    energy_kwh: float
    generated_at: str


class FleetSignalRange(BaseModel):
    members: list[FleetSignalModel]
    low_kwh: float
    high_kwh: float
    spread_percent: float | None


class FleetSignalResponse(BaseModel):
    date: str
    provinces: list[str]
    generated_at: str | None
    previous_generated_at: str | None
    basis: ForecastBasis | None
    solar: FleetSignalPair | None
    wind: FleetSignalPair | None
    combined: FleetSignalPair | None
    hours: list[FleetSignalHour]
    top_windows: list[FleetSignalWindow]
    regions: list[FleetRegionSignal]
    evolution: list[FleetSignalIssuance]
    model_range: FleetSignalRange | None
    reason: str | None
