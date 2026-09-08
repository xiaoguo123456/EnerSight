"""首页聚合。docs/06 §六

一个请求覆盖首页全部模块：站点、指数、当前天气、趋势、预警。
"""

import asyncio
import math

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Station
from app.schemas.common import Coord, EnergyIndex, IndexLevel, MetricWithDelta
from app.schemas.home import (
    CurrentWeather,
    HomeResponse,
    TrendMetric,
    TrendPoint,
    TrendRange,
    TrendSeries,
)
from app.schemas.station import StationMetrics
from app.services import energy, weather
from app.services.station import get_station, to_summary
from app.services.weather_text import describe_transition

_TREND_SPEC: dict[TrendMetric, tuple[str, str, float | None]] = {
    # metric → (Open-Meteo 列, 单位, 固定纵轴上限)
    TrendMetric.RADIATION: ("shortwave_radiation", "W/m²", 1000.0),
    TrendMetric.WIND_SPEED: ("wind_speed_10m", "m/s", None),
    TrendMetric.CLOUD_COVER: ("cloud_cover", "%", 100.0),
}


def _num(v) -> float | None:
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) else f


def _delta(now: float | None, yesterday: float | None) -> float | None:
    """较昨日同期。昨值缺失或为 0 时返回 None，前端隐藏标签，不展示 0% 或 ∞。docs/07 §3.3"""
    if now is None or yesterday is None or yesterday == 0:
        return None
    return round((now - yesterday) / abs(yesterday) * 100, 1)


def build_current_weather(fc: weather.Forecast) -> CurrentWeather | None:
    now_ts = fc.current_hour()
    row = fc.at(now_ts)
    if row is None:
        return None
    y_row = fc.at(now_ts - pd.Timedelta(days=1))
    later = fc.at(now_ts + pd.Timedelta(hours=6))

    def m(col: str) -> MetricWithDelta:
        v = _num(row.get(col))
        y = _num(y_row.get(col)) if y_row is not None else None
        return MetricWithDelta(value=v, delta_percent=_delta(v, y))

    return CurrentWeather(
        temperature=m("temperature_2m"),
        apparent_temperature=_num(row.get("apparent_temperature")),
        humidity=_num(row.get("relative_humidity_2m")),
        wind_speed=m("wind_speed_10m"),
        wind_direction=_num(row.get("wind_direction_10m")),
        cloud_cover=m("cloud_cover"),
        radiation=m("shortwave_radiation"),
        weather_text=describe_transition(
            _num(row.get("weather_code")),
            _num(later.get("weather_code")) if later is not None else None,
        ),
        observed_at=now_ts.isoformat(),
    )


def build_trend(fc: weather.Forecast, metric: TrendMetric) -> TrendSeries:
    col, unit, y_max = _TREND_SPEC[metric]
    df = fc.today_with_midnight()
    points = [TrendPoint(time=ts.isoformat(), value=_num(v)) for ts, v in df[col].items()]
    return TrendSeries(metric=metric, unit=unit, range=TrendRange.H24, y_max=y_max, points=points)


def build_index(snap: energy.EnergySnapshot, station_type: str) -> EnergyIndex:
    r = snap.index
    return EnergyIndex(
        score=r.score,
        level=IndexLevel(r.level.value),
        summary=energy.summary_text(r, station_type),
        estimated=False,
        attribution=r.attribution,
    )


async def build_home(
    db: AsyncSession,
    http: httpx.AsyncClient,
    owner_id: str,
    coord: Coord,
    station_id: str | None,
) -> HomeResponse:
    if station_id:
        station = await get_station(db, owner_id, station_id)
    else:
        # 未指定时取用户最早创建的站点
        station = (
            (
                await db.execute(
                    select(Station).where(Station.owner_id == owner_id).order_by(Station.created_at)
                )
            )
            .scalars()
            .first()
        )
        if station is None:
            return HomeResponse(
                has_station=False, station=None, index=None, weather=None, trends=None, alert=None
            )

    fc = await weather.get_forecast(http, station.latitude, station.longitude)

    # pvlib 是 CPU 密集同步代码，丢进线程池，别卡事件循环。docs/05 §6.6
    loop = asyncio.get_running_loop()
    snap = await loop.run_in_executor(None, energy.compute, station, fc)

    summary = to_summary(station, coord)
    # 累计发电与减排需要逐日累积的历史，那是定时任务的事；有了再填
    summary.metrics = StationMetrics(
        daily_generation=round(snap.daily_kwh, 1),
        current_power=round(snap.current_kw, 1) if snap.current_kw is not None else None,
        total_generation=None,
        co2_reduction=None,
    )

    return HomeResponse(
        has_station=True,
        station=summary,
        index=build_index(snap, station.type),
        weather=build_current_weather(fc),
        trends=build_trend(fc, TrendMetric.RADIATION),
        # 预警规则属于下一个里程碑，此处按契约返回 null，前端不渲染预警条
        alert=None,
    )
