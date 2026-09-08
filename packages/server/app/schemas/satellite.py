"""卫星云图接口。docs/06 §7.3"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.layer import LatLng, LayerImage, Legend


class SatelliteCloudResponse(BaseModel):
    band: Literal["visible", "infrared", "vapor"] = Field(
        description="服务端按太阳高度角自动选择：白天 visible（真彩展示），夜间 infrared"
    )
    observed_at: str = Field(description="卫星观测时间，不是请求时间")
    image: LayerImage
    legend: Legend
    station_marker: LatLng = Field(description="已按 coord 转换")
