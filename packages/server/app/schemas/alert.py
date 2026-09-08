"""预警接口。docs/06 §九"""

from pydantic import BaseModel, Field

from app.schemas.home import AlertSummary


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
    # satellite 字段等云图接口落地后加入
