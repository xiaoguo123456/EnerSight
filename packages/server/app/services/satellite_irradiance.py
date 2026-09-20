"""卫星辐照实况：葵花 L2 短波辐射按电站坐标取值。docs/19 §四

数据链路：Buffalo 上的定时任务每 10 分钟把 JAXA P-Tree 的 L2 SWR 拉进自建 Open-Meteo
（扫描时刻校正、瞬时值换成前 10 分钟均值都由它做掉），这里只按坐标查出来。

两条硬约定：

- **查询必须带 `temporal_resolution=native`**，否则只回小时均值，实况要等整点过完才有。
- **不退回官方**：葵花辐射只在自建实例里，官方那条路没有这个模型，退过去只会拿到 400。

当前只做展示（预警页「卫星实况」卡）。当前功率替换与当日累计订正要等 PVOD 回测达标
（[07 §8.1]），在那之前卫星值不参与任何计算。
"""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.cache import AsyncTTLCache
from app.config import settings
from app.metrics import solar
from app.models import Station
from app.providers.open_meteo import OpenMeteoProvider
from app.schemas.satellite import SatelliteIrradiance
from app.services.satellite import is_day

log = logging.getLogger(__name__)

# 署名：JAXA 研究数据条款要求衍生展示注明来源，见 docs/19 §四「授权」
SOURCE = "JAXA / P-Tree 葵花卫星"
STEP_MINUTES = 10

_cache = AsyncTTLCache(maxsize=512, ttl_seconds=settings.ttl_satellite_irradiance)


def _none(status: str) -> SatelliteIrradiance:
    return SatelliteIrradiance(
        ghi_w_m2=None,
        direct_w_m2=None,
        diffuse_w_m2=None,
        clear_sky_ghi_w_m2=None,
        clear_sky_index=None,
        observed_at=None,
        status=status,  # type: ignore[arg-type]
        source=SOURCE,
    )


def _latest(raw: dict) -> tuple[datetime, float, float | None, float | None] | None:
    """最近一个有值的 10 分钟格。时间标签是区间末，与预报辐射同口径。"""
    block = raw.get("hourly")
    if not isinstance(block, dict):
        return None
    times = block.get("time") or []
    ghi = block.get("shortwave_radiation") or []
    direct = block.get("direct_radiation") or []
    diffuse = block.get("diffuse_radiation") or []
    for i in range(len(times) - 1, -1, -1):
        value = ghi[i] if i < len(ghi) else None
        if value is None:
            continue
        when = datetime.fromisoformat(str(times[i])).replace(tzinfo=UTC)
        return (
            when,
            float(value),
            float(direct[i]) if i < len(direct) and direct[i] is not None else None,
            float(diffuse[i]) if i < len(diffuse) and diffuse[i] is not None else None,
        )
    return None


async def _fetch(http: httpx.AsyncClient, station: Station) -> dict:
    now = datetime.now(UTC)
    # 跨 UTC 零点时最近一帧还在昨天，两天一起要；native 下一天 144 格，量很小
    return await OpenMeteoProvider(http).satellite_radiation(
        station.latitude,
        station.longitude,
        start=(now - timedelta(days=1)).date(),
        end=now.date(),
    )


async def get(http: httpx.AsyncClient, station: Station, tz: str) -> SatelliteIrradiance | None:
    """这座电站此刻的卫星辐照。关了开关返回 None；其余情况一律给对象 + status。"""
    if not settings.satellite_irradiance_enabled:
        return None
    now = datetime.now(UTC)
    if not is_day(station.latitude, station.longitude, now):
        # 夜间没有可见光反演，不是故障
        return _none("night")
    key = f"sat-ghi:{station.latitude:.2f},{station.longitude:.2f}"

    async def _load() -> SatelliteIrradiance:
        try:
            raw = await _fetch(http, station)
        except Exception as exc:  # noqa: BLE001  卫星是锦上添花，任何失败都只降级
            log.warning("卫星辐照取数失败：%s", exc)
            return _none("unavailable")
        latest = _latest(raw)
        if latest is None:
            return _none("unavailable")
        observed, ghi, direct, diffuse = latest
        if (now - observed) > timedelta(minutes=settings.satellite_irradiance_stale_minutes):
            return _none("stale")
        # 晴空基准与实测同口径：观测时刻往前一个 10 分钟区间的均值
        cs = solar.clearsky_interval_mean(
            station.latitude,
            station.longitude,
            tz,
            pd.DatetimeIndex([observed.astimezone(UTC)]).tz_convert(tz),
            STEP_MINUTES,
        )
        clear = float(cs["ghi"].iloc[0])
        return SatelliteIrradiance(
            ghi_w_m2=round(ghi, 1),
            direct_w_m2=round(direct, 1) if direct is not None else None,
            diffuse_w_m2=round(diffuse, 1) if diffuse is not None else None,
            clear_sky_ghi_w_m2=round(clear, 1) if clear > 0 else None,
            clear_sky_index=round(ghi / clear, 3) if clear > 0 else None,
            observed_at=observed.astimezone(ZoneInfo(tz)).isoformat(),
            status="ok",
            source=SOURCE,
        )

    return await _cache.get_or_load(key, _load)
