"""发电预测：电量与功率分开，覆盖不完整时不宣称全量。"""

from pydantic import BaseModel, Field


class PowerPoint(BaseModel):
    time: str
    value: float | None


class GenerationPrediction(BaseModel):
    model: str
    date: str
    timezone: str = "Asia/Shanghai"
    generated_at: str
    energy_kwh: float | None
    power_kw: list[PowerPoint]
    assumptions: list[str] = Field(default_factory=list)


class RegionPrediction(BaseModel):
    province: str
    energy_kwh: float
    covered_count: int


class FleetPrediction(GenerationPrediction):
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
    message: str | None = None
