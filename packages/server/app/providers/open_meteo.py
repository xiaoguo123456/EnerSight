"""Open-Meteo 适配。字段清单见 docs/04 §二。"""

from datetime import date

import httpx

from app.config import settings
from app.errors import DataUnavailable, UpstreamUnavailable

# 逐小时字段。新增字段前先更新 docs/04
HOURLY_FIELDS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "weather_code",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
    "global_tilted_irradiance",
]


class OpenMeteoProvider:
    """经纬度一律 WGS84 —— Open-Meteo 用的就是 WGS84，不做转换。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        forecast_days: int = 7,
        past_days: int = 1,
    ) -> dict:
        """逐小时预报。

        past_days=1 拿昨日数据，用于环比计算 —— 环比不能只靠实时数据
        推出来，见 docs/04「昨日同期对比数据」。
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": "auto",
            "forecast_days": forecast_days,
            "past_days": past_days,
        }
        return await self._get(f"{settings.open_meteo_base}/forecast", params)

    async def archive(self, latitude: float, longitude: float, start: date, end: date) -> dict:
        """历史气象。用于 docs/07 §八 的参数校准与分布检查。"""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        return await self._get(f"{settings.open_meteo_archive_base}/archive", params)

    async def _get(self, url: str, params: dict) -> dict:
        try:
            res = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable() from exc

        if res.status_code >= 500:
            raise UpstreamUnavailable()
        if res.status_code == 400:
            # Open-Meteo 对超出覆盖范围的坐标返回 400 —— 重试无用
            raise DataUnavailable()
        res.raise_for_status()
        return res.json()
