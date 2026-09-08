"""图层接口。docs/06 §7.2"""

from pydantic import BaseModel, Field

from app.schemas.common import LayerType


class LatLng(BaseModel):
    latitude: float
    longitude: float


class Bounds(BaseModel):
    sw: LatLng
    ne: LatLng


class LayerImage(BaseModel):
    url: str = Field(description="图片地址，带版本号")
    bounds: Bounds = Field(description="已按 coord 转换")


class LayerFrame(BaseModel):
    images: list[LayerImage] = Field(description="覆盖请求 bbox 所需的量化块")


class Legend(BaseModel):
    title: str
    type: str = Field(description="gradient | scale")
    stops: list[float] | None = Field(description="刻度值；无量纲图层为 null")
    labels: list[str] | None = Field(description="无量纲图层用，如 [低, 高]")
    colors: list[str]


class LayerResponse(BaseModel):
    layer: LayerType
    observed_at: str = Field(description="数据观测时间，非请求时间")
    unit: str | None
    legend: Legend
    frames: list[LayerFrame] = Field(description="静态图层长度为 1，风场为多帧")
    frame_interval_ms: int | None
