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


class SatelliteIrradiance(BaseModel):
    """卫星反演的地表辐照实况。docs/19 §四

    葵花 L2 短波辐射，5 km 格点、10 分钟一帧，时间标签是区间末的 10 分钟均值，
    与预报辐射同口径。只作实况展示，不参与指数与发电计算。
    """

    ghi_w_m2: float | None = Field(description="总辐照；非 ok 状态为 null")
    direct_w_m2: float | None
    diffuse_w_m2: float | None
    clear_sky_ghi_w_m2: float | None = Field(description="同时刻晴空基准，夜间与缺测为 null")
    clear_sky_index: float | None = Field(
        description="实况 / 晴空，0–1 之间；界面显示「晴空的 78%」"
    )
    observed_at: str | None = Field(description="卫星观测时刻，按站点当地时区；非 ok 状态为 null")
    status: Literal["ok", "night", "stale", "unavailable"] = Field(
        description="night 夜间无可见光反演；stale 最新一帧过旧；unavailable 上游拿不到"
    )
    source: str = Field(description="署名文案，界面必须展示")


class SatelliteHistoryResponse(BaseModel):
    times: list[str] = Field(description="近三小时可用卫星观测时刻，升序；缺帧不补造")
    band: Literal["visible"] = "visible"
    start_at: str
    end_at: str
