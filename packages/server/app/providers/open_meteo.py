"""Open-Meteo 适配。字段清单见 docs/04 §二。"""

import asyncio
from datetime import UTC, date, datetime

import httpx

from app.config import settings
from app.errors import DataUnavailable, UpstreamRateLimited, UpstreamUnavailable
from app.providers.weather_transport import weather_get
from app.weather_model import current_model

# 逐小时字段。新增字段前先更新 docs/04
# 辐射类是「前一小时平均值」，标在区间末；太阳位置要按区间中点算，见 metrics/solar
# 字段数直接决定 Open-Meteo 的计费权重：超过 10 个变量按比例计为多次调用。
# 只列真正有消费方的字段，加字段前先确认谁在读，并同步 docs/04 §二。
HOURLY_FIELDS = [
    # 出力模型必需（全目录 fleet_prediction.FIELDS 是其子集）
    "temperature_2m",
    "wind_speed_10m",
    "wind_speed_80m",  # 各高度层：风电轮毂高度风速按对数廓线层间插值，见 metrics/wind
    "wind_speed_100m",
    "wind_speed_120m",
    # ECMWF IFS 原生只有 10 / 100 / 200 m，80、120 m 由上游推算；轮毂高于 120 m 时靠它插值，
    # 不再按 100–120 m 斜率外推。全目录统一 100 m 轮毂，不请求此层。docs/07 §2.2
    "wind_speed_200m",
    "shortwave_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
    "surface_pressure",  # 风电空气密度修正：ρ = p / (R T)，见 metrics/wind
    # 预警与展示
    "cloud_cover",  # 云层预警阈值 + 首页
    "weather_code",  # AI 报告天气描述 + 首页
    "apparent_temperature",  # 首页体感
    "relative_humidity_2m",  # 首页湿度
    "wind_direction_10m",  # 首页风向
]

# 点预报统一取 15 分钟，字段与历史小时资料一致；国内数据由上游插值生成。
MINUTELY_FIELDS = HOURLY_FIELDS.copy()


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
        forecast_days: int = 8,
        past_days: int = 1,
        cell_selection: str | None = None,
    ) -> dict:
        """统一点预报：昨日同期、七天预测及第七天末区间所需的边界数据。

        cell_selection 只在海上风电时传 sea；不传即上游默认 land。
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "minutely_15": ",".join(MINUTELY_FIELDS),
            "timezone": "auto",
            "forecast_days": forecast_days,
            "past_days": past_days,
            "wind_speed_unit": "ms",
        }
        params["models"] = current_model.get()
        if cell_selection:
            params["cell_selection"] = cell_selection
        return await self._get(f"{settings.open_meteo_base}/forecast", params)

    async def recent_hourly(
        self,
        latitude: float,
        longitude: float,
        *,
        past_days: int,
        cell_selection: str | None = None,
    ) -> dict:
        """过去最多 92 天的逐小时点预报，给实测订正回算模型同期电量。docs/19 §三

        上游把过去每个小时拼成最近一轮的短时效预报，与线上用的是同一个模型；
        只要日电量，所以取逐小时、不取 15 分钟。字段与主请求相同，不新增计费权重。
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": "auto",
            "past_days": past_days,
            "forecast_days": 1,
            "wind_speed_unit": "ms",
            "models": current_model.get(),
        }
        if cell_selection:
            params["cell_selection"] = cell_selection
        return await self._get(f"{settings.open_meteo_base}/forecast", params)

    async def model_meta(self, slug: str) -> ModelMeta | None:
        """模型元数据。拿不到返回 None，不阻塞预报；调用方按「无法确认起报」处理。"""
        try:
            res = await weather_get(
                self._client, f"{settings.open_meteo_meta_base}/{slug}/static/meta.json"
            )
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
        """带退避重试。经本机代理出网时偶发 TLS 拒连/超时，不重试会白丢一个站一整轮。

        只重试传输错误与 5xx。429 是配额、400 是坐标越界，重试都无用 —— 对 429
        尤其有害：额度按天算，立刻重试只会烧得更快。
        """
        last: Exception | None = None
        for attempt in range(settings.upstream_retries):
            if attempt:
                await asyncio.sleep(settings.upstream_backoff_seconds * attempt)
            try:
                res = await weather_get(self._client, url, params=params)
            except httpx.HTTPError as exc:
                last = exc
                continue
            if res.status_code == 429:
                # 冷却范围已由 weather_transport 按限流窗口处理，这里只转成业务错误。
                raise UpstreamRateLimited()
            if res.status_code == 400:
                raise DataUnavailable()
            if res.status_code >= 500:
                last = None
                continue
            res.raise_for_status()
            return res.json()
        raise UpstreamUnavailable() from last
