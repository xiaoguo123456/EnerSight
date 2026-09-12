"""气象数据层：拉取、缓存、切片。

所有下游（指数、趋势、当前天气）都从这里拿 Forecast，
不各自去调 Open-Meteo。
"""

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.cache import AsyncTTLCache, grid_key
from app.config import settings
from app.providers.open_meteo import ModelMeta, OpenMeteoProvider
from app.schemas.prediction import ForecastBasis
from app.weather_model import current_model

log = logging.getLogger(__name__)

# 缓存键带批次指纹，所以 TTL 按「一批数据的寿命」设，不是按新鲜度设：
# 同一批数据重复拉没有任何意义，新批次一落地键就换了。maxsize 是内存上限，
# 被 LRU 挤掉的条目下次访问重拉即可。
_cache = AsyncTTLCache(maxsize=2048, ttl_seconds=settings.ttl_forecast_batch)
_quarter_cache = AsyncTTLCache(maxsize=512, ttl_seconds=settings.ttl_forecast_batch)
_meta_cache = AsyncTTLCache(maxsize=16, ttl_seconds=settings.ttl_model_meta)


@dataclass(frozen=True)
class Forecast:
    """逐小时预报，昨日 00:00 起共 192 点（昨日 + 7 天），索引为站点当地时区的 tz-aware 时间。

    model / meta / fetched_at 说明这份数据来自哪一批模型输出（docs/17 §二）。
    meta 为 None 表示无法确认起报，basis() 里 issued_at 为 null。
    """

    tz: str
    hourly: pd.DataFrame
    model: str = "best_match"
    meta: ModelMeta | None = field(default=None, compare=False)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC), compare=False)
    # 15 分钟序列（上游插值），只有 forecast 接口带；archive 与旧缓存为 None，调用方退回逐小时
    quarter: pd.DataFrame | None = field(default=None, compare=False)
    # 站点海拔（Open-Meteo 90 m DEM），风电空气密度的降级来源
    elevation: float | None = None

    def basis(self) -> ForecastBasis:
        from app.services.model_resolution import resolve

        tz = ZoneInfo(self.tz)
        return ForecastBasis(
            model=self.model,
            resolved_model=self.meta.slug if self.meta else resolve(self.model),
            issued_at=self.meta.issued_at.astimezone(tz).isoformat() if self.meta else None,
            available_at=(
                self.meta.available_at.astimezone(tz).isoformat()
                if self.meta and self.meta.available_at
                else None
            ),
            fetched_at=self.fetched_at.astimezone(tz).isoformat(),
        )

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


def parse_quarter(raw: dict) -> pd.DataFrame | None:
    """响应里的 minutely_15 → DataFrame。没有该段返回 None，调用方退回逐小时。"""
    q = raw.get("minutely_15")
    if not isinstance(q, dict) or not q.get("time"):
        return None
    idx = pd.DatetimeIndex(pd.to_datetime(q["time"])).tz_localize(raw["timezone"])
    return pd.DataFrame({k: v for k, v in q.items() if k != "time"}, index=idx)


def parse_forecast(
    raw: dict, *, model: str | None = None, meta: ModelMeta | None = None
) -> Forecast:
    tz = raw["timezone"]
    h = raw["hourly"]
    idx = pd.DatetimeIndex(pd.to_datetime(h["time"])).tz_localize(tz)
    df = pd.DataFrame({k: v for k, v in h.items() if k != "time"}, index=idx)
    elev = raw.get("elevation")
    return Forecast(
        tz=tz,
        hourly=df,
        model=model or current_model.get(),
        meta=meta,
        # 同一份响应里带了 minutely_15 就直接收下（合成夹具、历史缓存走这条）
        quarter=parse_quarter(raw),
        elevation=float(elev) if isinstance(elev, int | float) else None,
    )


async def get_model_meta(http: httpx.AsyncClient, model: str) -> ModelMeta | None:
    """模型元数据，全局缓存几分钟，不按站点。无法解析到具体模型时为 None。"""
    from app.services.model_resolution import resolve

    slug = resolve(model)
    if slug is None:
        return None

    async def _load() -> ModelMeta | object:
        meta = await OpenMeteoProvider(http).model_meta(slug)
        return meta if meta is not None else _MISSING

    value = await _meta_cache.get_or_load(slug, _load)
    return value if isinstance(value, ModelMeta) else None


_MISSING = object()  # 缓存里的「拿过但没拿到」，避免每次请求都重打元数据接口


def batch_stamp(meta: ModelMeta | None) -> str:
    """一批模型输出的指纹，进缓存键。

    上游每 6 小时才出一批（00/06/12/18 UTC），按时间片缓存等于反复拉同一份数据：
    10 分钟 TTL 下每个网格每天回源 96 次，其中 92 次拿回来的字节完全相同。
    按起报时刻做键，新批次一落地键就变、立刻刷新，同批次内永远命中。

    元数据拿不到时**必须**退回时间片 —— 固定字符串会让这个网格的数据再也不刷新。
    """
    if meta is not None:
        return meta.issued_at.isoformat()
    return f"unknown-{int(time.time()) // settings.ttl_current_weather}"


async def get_forecast(
    http: httpx.AsyncClient, latitude: float, longitude: float, *, fine: bool = False
) -> Forecast:
    """按 0.1° 网格 + 模型批次缓存。相邻站点、同一批次命中同一份。

    fine=True 额外拉 15 分钟序列（单站 7 天预测的细粒度曲线）。它单独计费、
    单独缓存，拿不到就退回逐小时，不影响主链路。
    """
    model = current_model.get()
    # 先取元数据再取预报：两次调用之间若有新批次落地，元数据只会偏旧，不会冒充更新
    meta = await get_model_meta(http, model)
    cell = grid_key(latitude, longitude)
    key = f"{model}:{batch_stamp(meta)}:{cell}"

    async def _load() -> Forecast:
        raw = await OpenMeteoProvider(http).forecast(latitude, longitude)
        return parse_forecast(raw, model=model, meta=meta)

    base: Forecast = await _cache.get_or_load(key, _load)
    if not fine or base.quarter is not None:
        return base
    return replace(base, quarter=await _get_quarter(http, latitude, longitude, key))


async def _get_quarter(
    http: httpx.AsyncClient, latitude: float, longitude: float, key: str
) -> pd.DataFrame | None:
    """15 分钟序列。失败不入缓存：这是用户触发的低频路径，宁可下次重试也不要一坏坏一批。"""

    async def _load() -> pd.DataFrame | None:
        raw = await OpenMeteoProvider(http).quarter(
            latitude, longitude, forecast_days=settings.outlook_fine_days + 1
        )
        return parse_quarter(raw)

    try:
        return await _quarter_cache.get_or_load(key, _load)
    except Exception:  # noqa: BLE001  退回逐小时，prepare 里已有降级分支
        log.warning("15 分钟序列拉取失败，退回逐小时：%s", key, exc_info=True)
        return None


def clear_cache() -> None:
    _cache.clear()
    _quarter_cache.clear()
    _meta_cache.clear()
