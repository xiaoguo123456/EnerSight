"""预警接口。docs/06 §九"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.home import AlertSummary
from app.schemas.satellite import SatelliteCloudResponse


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


class CurrentAlertResponse(BaseModel):
    alert: AlertSummary | None
    cloud_motion: CloudMotion | None = Field(description="仅卫星短临预警有；预报类为 null")
    satellite: SatelliteCloudResponse | None = Field(description="夜间或上游不可用时为 null")
    satellite_status: Literal["ok", "night", "unavailable"] = Field(
        description="satellite 为 null 的原因：night 夜间无可见光；unavailable 数据源暂时拿不到"
    )
