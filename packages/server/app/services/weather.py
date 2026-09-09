"""气象数据层：拉取、缓存、切片。

所有下游（指数、趋势、当前天气）都从这里拿 Forecast，
不各自去调 Open-Meteo。
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.cache import AsyncTTLCache, grid_key
from app.config import settings
from app.providers.open_meteo import OpenMeteoProvider
from app.weather_model import current_model

_cache = AsyncTTLCache(maxsize=2048, ttl_seconds=settings.ttl_current_weather)


@dataclass(frozen=True)
class Forecast:
    """逐小时预报，昨日 00:00 起共 192 点（昨日 + 7 天），索引为站点当地时区的 tz-aware 时间。"""

    tz: str
    hourly: pd.DataFrame

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.tz))

    def current_hour(self) -> pd.Timestamp:
        """瞬时量的当前标签。

        气温、湿度、风速、云量、weather_code 标注时刻即观测时刻，取已过去的整点。
        """
        return pd.Timestamp(self.now()).floor("h")

    def current_interval(self) -> pd.Timestamp:
        """区间均值量的当前标签：包含当前时刻的那一小时区间。

        辐射（以及由辐射推出的光伏出力、晴空指数）是前一小时均值标在区间末，
        13:00 的值覆盖 12:00–13:00。用 floor 取会拿到已经过去的那一小时 ——
        日出后一小时内低估、日落后一小时内高估，早晚各差几倍。
        整点时刻 floor 与 ceil 相同，取到的是刚结束的完整区间。
        """
        return pd.Timestamp(self.now()).ceil("h")

    def today(self) -> pd.DataFrame:
        """今日 00:00 – 23:00，24 点"""
        d = self.current_hour().normalize()
        return self.hourly.loc[d : d + pd.Timedelta(hours=23)]

    def today_with_midnight(self) -> pd.DataFrame:
        """今日 00:00 – 明日 00:00，25 点，趋势图用"""
        d = self.current_hour().normalize()
        return self.hourly.loc[d : d + pd.Timedelta(hours=24)]

    def next_days(self, days: int, step_hours: int) -> pd.DataFrame:
        """今日 00:00 起 days 天，每 step_hours 取一点。7 天趋势用（7 × 8 = 56 点）。"""
        d = self.current_hour().normalize()
        window = self.hourly.loc[d : d + pd.Timedelta(days=days) - pd.Timedelta(hours=1)]
        return window.iloc[::step_hours]

    def at(self, ts: pd.Timestamp) -> pd.Series | None:
        try:
            return self.hourly.loc[ts]
        except KeyError:
            return None


def parse_forecast(raw: dict) -> Forecast:
    tz = raw["timezone"]
    h = raw["hourly"]
    idx = pd.DatetimeIndex(pd.to_datetime(h["time"])).tz_localize(tz)
    df = pd.DataFrame({k: v for k, v in h.items() if k != "time"}, index=idx)
    return Forecast(tz=tz, hourly=df)


async def get_forecast(http: httpx.AsyncClient, latitude: float, longitude: float) -> Forecast:
    """按 0.1° 网格缓存 10 分钟。相邻站点命中同一份。"""
    key = f"{current_model.get()}:{grid_key(latitude, longitude)}"

    async def _load() -> Forecast:
        raw = await OpenMeteoProvider(http).forecast(latitude, longitude)
        return parse_forecast(raw)

    return await _cache.get_or_load(key, _load)


def clear_cache() -> None:
    _cache.clear()
