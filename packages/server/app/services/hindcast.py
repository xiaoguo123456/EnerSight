"""模型同期电量：回算过去某一天，线上模型会给这座电站算出多少电。docs/19 §三

拟合订正系数要拿实测去比模型。比较过三个来源：
- 预测留档：本版才开始每天签发，用户补录上个月结算单时根本没有留档；
- ERA5 再分析：和线上预报不是同一个模型，拟出的系数修的是再分析的偏差；
- **预报接口 past_days**：同一个 best_match 模型，上游把过去每个小时拼成最近一轮的短时效预报，
  最多回溯 92 天。取这个。

逐日走 `energy.prepare(day=…)` 与 `energy.hourly_power` 同一条链路，不另写一份出力计算。
设了出力约束的电站取计入约束后的「预计上网」，免得把限电学进系数。
"""

import asyncio
import hashlib
import json
from datetime import date, timedelta

import httpx
import numpy as np

from app.cache import AsyncTTLCache
from app.config import settings
from app.models import Station
from app.providers.open_meteo import OpenMeteoProvider
from app.services import curtailment, energy
from app.services.prediction_basis import VERSION, calculation_parameters, station_parameters
from app.services.weather import Forecast, parse_forecast
from app.weather_model import current_model

MODEL = "best_match"
_cache = AsyncTTLCache(maxsize=256, ttl_seconds=settings.ttl_hindcast)


def digest(station: Station) -> str:
    """模型值的参数指纹：设备、容量、约束或计算参数变了，旧的模型值就不能再拿来拟合。"""
    payload = {"station": station_parameters(station), "calc": calculation_parameters()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[
        :16
    ]


# 回溯天数分档：最常见的是记昨天，只拉几天就够；补录一个多月才拉满 92 天
SPANS = (7, 31, 92)


def span_for(days_back: int) -> int:
    for span in SPANS:
        if days_back <= span:
            return min(span, settings.hindcast_max_days)
    return settings.hindcast_max_days


async def weather(
    http: httpx.AsyncClient, station: Station, days_back: int | None = None
) -> Forecast:
    """过去若干天的逐小时气象，按档取，同一电站当天同一档共用。"""
    selection = getattr(station, "_cell_selection", None)
    span = span_for(days_back or settings.hindcast_max_days)
    key = (
        f"hind:{station.latitude!r},{station.longitude!r}:{selection or ''}:"
        f"{date.today().isoformat()}:{span}"
    )

    async def _load() -> Forecast:
        # 拟合与线上预报必须是同一个模型；请求上下文里可能是用户选的别的模型，这里固定
        token = current_model.set(MODEL)
        try:
            raw = await OpenMeteoProvider(http).recent_hourly(
                station.latitude,
                station.longitude,
                past_days=span,
                cell_selection=selection,
            )
        finally:
            current_model.reset(token)
        return parse_forecast(raw, model=MODEL)

    return await _cache.get_or_load(key, _load)


def day_kwh(station: Station, fc: Forecast, day: date) -> float | None:
    """某一天的模型电量（kWh，未订正）。缺测、容量口径待核验或超出回溯范围时为 None。"""
    today = fc.now().date()
    if day >= today or (today - day).days > settings.hindcast_max_days:
        return None
    prep = energy.prepare(station, fc, day=day, version=VERSION)
    if not prep.complete or prep.blocked:
        return None
    hourly = energy.hourly_power(station, prep, fc.tz)
    rule = curtailment.parse(getattr(station, "curtailment", None))
    if rule is not None:
        hourly = curtailment.apply(
            hourly,
            rule,
            station.capacity_kw,
            interval_end=station.type == "solar",
            step_minutes=prep.step_minutes,
        )
    values = hourly.to_numpy(dtype=float)
    if values.size == 0 or not np.isfinite(values).all():
        return None
    return round(float(values.sum()) * prep.step_minutes / 60.0, 2)


def period_kwh(station: Station, fc: Forecast, start: date, end: date) -> float | None:
    """一段日期（含两端）的模型电量合计。任何一天算不出，整段就是 None —— 不拿半个月去比一个月。"""
    total = 0.0
    day = start
    while day <= end:
        v = day_kwh(station, fc, day)
        if v is None:
            return None
        total += v
        day += timedelta(days=1)
    return round(total, 2)


async def periods_kwh(
    http: httpx.AsyncClient, station: Station, periods: list[tuple[date, date]]
) -> dict[tuple[date, date], float | None]:
    """批量回算。气象只拉一次；逐日计算是 pvlib，丢进线程池。"""
    if not periods:
        return {}
    back = max((date.today() - start).days for start, _ in periods) + 1
    fc = await weather(http, station, back)

    def _run() -> dict[tuple[date, date], float | None]:
        return {p: period_kwh(station, fc, p[0], p[1]) for p in periods}

    return await asyncio.to_thread(_run)
