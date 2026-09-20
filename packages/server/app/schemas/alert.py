"""预警接口。docs/06 §九"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.home import AlertSummary
from app.schemas.satellite import SatelliteCloudResponse, SatelliteIrradiance
from app.schemas.station import StationSummary


class AlertListResponse(BaseModel):
    alerts: list[AlertSummary]
    next_cursor: str | None = Field(description="下一页游标，null 表示没有更多")


class CloudMotion(BaseModel):
    """云团短临外推三宫格。仅卫星短临预警有。docs/07 §四"""

    distance_km: float
    direction: str
    direction_detail: str
    impact_in_minutes: int
    impact_start_time: str
    reference_station: str
    observed_at: str | None = None
    impact_start_at: str | None = None


class CurrentAlertResponse(BaseModel):
    station: StationSummary
    checked_at: str = Field(description="气象规则检查时间，ISO 8601")
    alert: AlertSummary | None
    cloud_motion: CloudMotion | None = Field(description="仅卫星短临预警有；预报类为 null")
    satellite: SatelliteCloudResponse | None = Field(description="上游不可用时为 null")
    satellite_status: Literal["ok", "unavailable"] = Field(
        description="unavailable 表示数据源暂时拿不到；夜间有红外，不再是空态"
    )
    irradiance: SatelliteIrradiance | None = Field(
        description="卫星辐照实况；关掉开关时为 null，其余情况看 irradiance.status。docs/19 §四"
    )
