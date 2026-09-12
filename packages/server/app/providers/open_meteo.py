"""Open-Meteo 适配。字段清单见 docs/04 §二。"""

from datetime import UTC, date, datetime

import httpx

from app.config import settings
from app.errors import DataUnavailable, UpstreamUnavailable
from app.weather_model import current_model

# 逐小时字段。新增字段前先更新 docs/04
# 辐射类是「前一小时平均值」，标在区间末；太阳位置要按区间中点算，见 metrics/solar
HOURLY_FIELDS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_speed_80m",  # 80 / 100 / 120 m：风电轮毂高度风速按对数廓线插值，见 metrics/wind
    "wind_speed_100m",
    "wind_speed_120m",
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
    "is_day",
]


# 请求模型 → 元数据 slug。gfs_global 由 0.13° 与 0.25° 两套拼成，起报不同，以 0.13° 为准。
# best_match 在境内实测等于 ecmwf_ifs，由 services.model_resolution 每日复核。docs/17 §二
META_SLUGS: dict[str, str] = {
    "ecmwf_ifs": "ecmwf_ifs",
    "gfs_global": "ncep_gfs013",
    "icon_global": "dwd_icon",
}


class ModelMeta:
    """一批模型数据的起报与可用时刻，来自 {meta_base}/{slug}/static/meta.json。"""

    __slots__ = ("available_at", "issued_at", "slug", "update_interval_seconds")

    def __init__(
        self,
        slug: str,
        issued_at: datetime,
        available_at: datetime | None,
        update_interval_seconds: int | None,
    ) -> None:
        self.slug = slug
        self.issued_at = issued_at
        self.available_at = available_at
        self.update_interval_seconds = update_interval_seconds

    @classmethod
    def parse(cls, slug: str, raw: dict) -> "ModelMeta | None":
        init = raw.get("last_run_initialisation_time")
        if not isinstance(init, int | float):
            return None
        avail = raw.get("last_run_availability_time")
        interval = raw.get("update_interval_seconds")
        return cls(
            slug,
            datetime.fromtimestamp(float(init), UTC),
            datetime.fromtimestamp(float(avail), UTC) if isinstance(avail, int | float) else None,
            int(interval) if isinstance(interval, int | float) else None,
        )


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
        """逐小时预报，默认 昨日 + 未来 7 天 共 192 点。

        past_days=1 拿昨日数据，用于环比计算 —— 环比不能只靠实时数据
        推出来，见 docs/04「昨日同期对比数据」。
        forecast_days=7 供 7 天趋势；24 小时趋势要到「明日 00:00」这一点也包含在内。

        wind_speed_unit=ms 必须传：Open-Meteo 默认 km/h，漏了风速会错 3.6 倍。
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": "auto",
            "forecast_days": forecast_days,
            "past_days": past_days,
            "wind_speed_unit": "ms",
        }
        params["models"] = current_model.get()
        return await self._get(f"{settings.open_meteo_base}/forecast", params)

    async def model_meta(self, slug: str) -> ModelMeta | None:
        """模型元数据。拿不到返回 None，不阻塞预报；调用方按「无法确认起报」处理。"""
        try:
            res = await self._client.get(f"{settings.open_meteo_meta_base}/{slug}/static/meta.json")
            if res.status_code != 200:
                return None
            raw = res.json()
        except Exception:  # noqa: BLE001  元数据是锦上添花，任何失败都不影响主链路
            return None
        return ModelMeta.parse(slug, raw) if isinstance(raw, dict) else None

    async def archive(self, latitude: float, longitude: float, start: date, end: date) -> dict:
        """历史气象。用于 docs/07 §八 的参数校准与分布检查。"""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "wind_speed_unit": "ms",
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
