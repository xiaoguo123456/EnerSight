"""气象数据层：拉取、缓存、切片。

所有下游（指数、趋势、当前天气）都从这里拿 Forecast，
不各自去调 Open-Meteo。
"""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.cache import AsyncTTLCache
from app.config import settings
from app.providers.open_meteo import ModelMeta, OpenMeteoProvider
from app.schemas.prediction import ForecastBasis
from app.weather_model import current_model

# 每个坐标、模型一条缓存，新鲜度由 get_forecast 按回源时段与起报批次判断，
# TTL 只是一天的内存兜底。maxsize 是内存上限，被 LRU 挤掉的条目下次访问重拉即可。
_cache = AsyncTTLCache(maxsize=512, ttl_seconds=86400)
_meta_cache = AsyncTTLCache(maxsize=16, ttl_seconds=settings.ttl_model_meta)


@dataclass(frozen=True)
class Forecast:
    """统一点预报；历史小时输入仍保留原始分辨率，不伪造 15 分钟历史。

    model / meta / fetched_at 说明这份数据来自哪一批模型输出（docs/17 §二）。
    meta 为 None 表示无法确认起报，basis() 里 issued_at 为 null。
    """

    tz: str
    hourly: pd.DataFrame = field(default_factory=pd.DataFrame)
    model: str = "best_match"
    meta: ModelMeta | None = field(default=None, compare=False)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC), compare=False)
    # 兼容旧小时留档；线上只保存 quarter，data 是各业务唯一读取入口。
    quarter: pd.DataFrame | None = field(default=None, compare=False)
    # 站点海拔（Open-Meteo 90 m DEM），风电空气密度的降级来源
    elevation: float | None = None

    @property
    def data(self) -> pd.DataFrame:
        return self.quarter if self.quarter is not None else self.hourly

    @property
    def step_minutes(self) -> int:
        return 15 if self.quarter is not None else 60

    def basis(self) -> ForecastBasis:
        from app.services.model_resolution import resolve

        tz = ZoneInfo(self.tz)
        return ForecastBasis(
            model=self.model,
            resolved_model=(
                self.meta.slug
                if self.meta
                else (resolve(self.model) if self.model != "best_match" else None)
            ),
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

        气温、湿度、风速、云量、weather_code 标注时刻即观测时刻。
        按输入步长取已过去的时刻（当前预报为 15 分钟）。
        """
        return pd.Timestamp(self.now()).floor(f"{self.step_minutes}min")

    def current_interval(self) -> pd.Timestamp:
        """区间均值量的当前标签：包含当前时刻的那个区间。

        辐射（以及由辐射推出的光伏出力、晴空指数）把区间均值标在区间末。
        15 分钟输入的 13:00 覆盖 12:45–13:00；历史小时输入覆盖 12:00–13:00。
        向上取整才能选中当前区间，避免日出后低估、日落后高估。
        恰好位于区间边界时取刚结束的完整区间。
        """
        return pd.Timestamp(self.now()).ceil(f"{self.step_minutes}min")

    def today(self) -> pd.DataFrame:
        """今日完整时间轴，15 分钟输入为 00:00–23:45 共 96 点。"""
        d = self.current_hour().normalize()
        return self.data.loc[d : d + pd.Timedelta(days=1) - pd.Timedelta(minutes=self.step_minutes)]

    def today_with_midnight(self) -> pd.DataFrame:
        """今日 00:00 至明日 00:00，15 分钟输入共 97 点，趋势图用。"""
        return self.day_with_midnight(0)

    def day_with_midnight(self, offset: int) -> pd.DataFrame:
        """今日起第 offset 天 00:00 至次日 00:00，与七天预测的日期一一对应。"""
        d = self.current_hour().normalize() + pd.Timedelta(days=offset)
        return self.data.loc[d : d + pd.Timedelta(hours=24)]

    def next_days(self, days: int) -> pd.DataFrame:
        """今日 00:00 起 days 天，保持输入时间步长，七天为 672 点。"""
        d = self.current_hour().normalize()
        window = self.data.loc[
            d : d + pd.Timedelta(days=days) - pd.Timedelta(minutes=self.step_minutes)
        ]
        return window

    def at(self, ts: pd.Timestamp) -> pd.Series | None:
        try:
            return self.data.loc[ts]
        except KeyError:
            return None


def parse_quarter(raw: dict) -> pd.DataFrame | None:
    """解析 15 分钟输入；没有该段时由线上调用方拒绝，历史解析可保留小时资料。"""
    q = raw.get("minutely_15")
    if not isinstance(q, dict) or not q.get("time"):
        return None
    idx = pd.DatetimeIndex(pd.to_datetime(q["time"])).tz_localize(raw["timezone"])
    return pd.DataFrame({k: v for k, v in q.items() if k != "time"}, index=idx)


def parse_forecast(
    raw: dict,
    *,
    model: str | None = None,
    meta: ModelMeta | None = None,
    require_quarter: bool = False,
) -> Forecast:
    tz = raw["timezone"]
    quarter = parse_quarter(raw)
    if require_quarter and quarter is None:
        from app.errors import UpstreamUnavailable

        raise UpstreamUnavailable("气象服务未返回 15 分钟预报")
    h = raw.get("hourly")
    df = pd.DataFrame()
    if quarter is None and h is not None:
        idx = pd.DatetimeIndex(pd.to_datetime(h["time"])).tz_localize(tz)
        df = pd.DataFrame({k: v for k, v in h.items() if k != "time"}, index=idx)
    elev = raw.get("elevation")
    return Forecast(
        tz=tz,
        hourly=df,
        model=model or current_model.get(),
        meta=meta,
        # 只保存一份输入，避免重复持有小时与 15 分钟序列
        quarter=quarter,
        elevation=float(elev) if isinstance(elev, int | float) else None,
    )


async def get_model_meta(
    http: httpx.AsyncClient, model: str, *, fresh: bool = False
) -> ModelMeta | None:
    """模型元数据，全局缓存几分钟，不按站点。无法解析到具体模型时为 None。"""
    from app.services.model_resolution import resolve

    slug = resolve(model)
    if slug is None:
        return None
    if fresh:
        return await OpenMeteoProvider(http).model_meta(slug)

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


def batch_settled(meta: ModelMeta | None) -> bool:
    """这批数据是不是已经整批落地了。

    数据桶按变量分别重写：`meta.json` 宣布新批次可用之后，还有一段时间里部分变量
    的滚动时序文件仍是上一批 —— 实测同 chunk 各变量 `Last-Modified` 跨度约 30 分钟。
    在这个窗口里换批次会取到混着两批的数据，而 `basis.issued_at` 会报成新批次，
    比实际数据新。所以窗口内继续用手上那批，等整批沉降完再换。

    只有自建才需要这条：官方接口对外宣布可用时，它自己那份库已经是一致的。
    拿不到 `available_at` 就没法判断，按已沉降处理（不能因此永不刷新）。
    """
    if meta is None or not settings.weather_self_hosted:
        return True
    if meta.available_at is None:
        return True
    age = (datetime.now(UTC) - meta.available_at).total_seconds()
    return age >= settings.weather_batch_settle_seconds


def refresh_slot(moment: datetime, tz: str) -> tuple[str, int]:
    """当地日期与当天第几个回源时段；一天按 forecast_refreshes_per_day 等分。"""
    local = moment.astimezone(ZoneInfo(tz))
    return local.date().isoformat(), local.hour * settings.forecast_refreshes_per_day // 24


async def get_forecast(
    http: httpx.AsyncClient,
    latitude: float,
    longitude: float,
    *,
    cell_selection: str | None = None,
) -> Forecast:
    """所有消费者共享同一份 15 分钟缓存，按坐标、格点选择与模型隔离。docs/04 §二

    cell_selection 为 None 时沿用上游默认（land）；海上风电传 sea，否则近岸场站会取到陆地格点。

    每个坐标每天最多回源 forecast_refreshes_per_day 次（默认 4，按当地时段均分）：
    同一时段内一律命中，不追新批次；进入新时段但起报批次没变仍命中；跨当地日必定刷新，
    第七天末边界与昨日同期都依赖当天的请求。元数据拿不到时无法比对批次，每个时段回源一次。
    """
    model = current_model.get()
    # 先取元数据再取预报：两次调用之间若有新批次落地，元数据只会偏旧，不会冒充更新
    meta = await get_model_meta(http, model)
    stamp = batch_stamp(meta) if meta is not None else None
    settled = batch_settled(meta)
    key = f"15m:{model}:{latitude!r},{longitude!r}" + (
        f":{cell_selection}" if cell_selection else ""
    )

    async def _load() -> Forecast:
        raw = await OpenMeteoProvider(http).forecast(
            latitude,
            longitude,
            forecast_days=settings.forecast_outlook_days + 1,
            cell_selection=cell_selection,
        )
        return parse_forecast(raw, model=model, meta=meta, require_quarter=True)

    def fresh(fc: Forecast) -> bool:
        fetched_day, fetched_slot = refresh_slot(fc.fetched_at, fc.tz)
        day, slot = refresh_slot(fc.now(), fc.tz)
        if fetched_day != day:
            return False
        if fetched_slot == slot:
            return True
        if stamp is not None and fc.meta is not None and batch_stamp(fc.meta) == stamp:
            return True
        # 新批次还没整批落地就先不换，免得取到混着两批的数据。见 batch_settled
        return fc.meta is not None and not settled

    return await _cache.get_or_load(key, _load, valid=fresh)


async def station_forecast(http: httpx.AsyncClient, station) -> Forecast:
    """站点的点预报。目录海上风电带 _cell_selection（services/station.from_catalog）。"""
    selection = getattr(station, "_cell_selection", None)
    if selection:
        return await get_forecast(
            http, station.latitude, station.longitude, cell_selection=selection
        )
    return await get_forecast(http, station.latitude, station.longitude)


def clear_cache() -> None:
    _cache.clear()
    _meta_cache.clear()
