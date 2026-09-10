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


class WindVector(BaseModel):
    latitude: float
    longitude: float
    u: float = Field(description="向东风速，m/s")
    v: float = Field(description="向北风速，m/s")


class ScalarSample(BaseModel):
    latitude: float
    longitude: float
    value: float


class LayerResponse(BaseModel):
    coverage: str | None = Field(default=None, description="空间覆盖范围及显示说明")
    source: str | None = None
    model: str | None = None
    resolution_km: float | None = None
    run_at: str | None = Field(default=None, description="预报起报批次，UTC")
    layer: LayerType
    observed_at: str = Field(description="数据观测时间，非请求时间")
    unit: str | None
    legend: Legend
    frames: list[LayerFrame] = Field(description="静态图层长度为 1，风场为多帧")
    frame_interval_ms: int | None
    wind_vectors: list[WindVector] | None = None
    stale: bool = False
    samples: list[ScalarSample] = Field(default_factory=list, description="预报采样点，非站点实测")
