"""首页聚合。docs/06 §六

一个请求覆盖首页全部模块：站点、指数、当前天气、趋势、预警。
"""

import asyncio
import math
from dataclasses import dataclass

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CatalogPlant, Station
from app.schemas.common import Coord, EnergyIndex, IndexLevel, MetricWithDelta
from app.schemas.home import (
    CurrentWeather,
    HomeResponse,
    TrendMetric,
    TrendPoint,
    TrendRange,
    TrendSeries,
)
from app.schemas.station import StationMetrics, StationSummary
from app.services import accumulate, alerts, energy, weather
from app.services.station import from_catalog, get_station, to_summary
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
    # 辐射是前一小时均值标在区间末，要取包含当前时刻的那一格；其余字段是瞬时值，用整点
    rad_ts = fc.current_interval()
    rad_row = fc.at(rad_ts)
    rad_y_row = fc.at(rad_ts - pd.Timedelta(days=1))

    def m(col: str) -> MetricWithDelta:
        v = _num(row.get(col))
        y = _num(y_row.get(col)) if y_row is not None else None
        return MetricWithDelta(value=v, delta_percent=_delta(v, y))

    def interval_m(col: str) -> MetricWithDelta:
        v = _num(rad_row.get(col)) if rad_row is not None else None
        y = _num(rad_y_row.get(col)) if rad_y_row is not None else None
        return MetricWithDelta(value=v, delta_percent=_delta(v, y))

    return CurrentWeather(
        temperature=m("temperature_2m"),
        apparent_temperature=_num(row.get("apparent_temperature")),
        humidity=_num(row.get("relative_humidity_2m")),
        wind_speed=m("wind_speed_10m"),
        wind_direction=_num(row.get("wind_direction_10m")),
        cloud_cover=m("cloud_cover"),
        radiation=interval_m("shortwave_radiation"),
        weather_text=describe_transition(
            _num(row.get("weather_code")),
            _num(later.get("weather_code")) if later is not None else None,
        ),
        observed_at=now_ts.isoformat(),
    )


def build_trend(
    fc: weather.Forecast, metric: TrendMetric, range_: TrendRange = TrendRange.H24
) -> TrendSeries:
    """24h 逐小时 25 点；7d 逐 3 小时 56 点（粒度待产品确认，docs/06 §十五）。"""
    col, unit, y_max = _TREND_SPEC[metric]
    if range_ == TrendRange.D7:
        df = fc.next_days(7, settings.trend_7d_step_hours)
    else:
        df = fc.today_with_midnight()
    points = [TrendPoint(time=ts.isoformat(), value=_num(v)) for ts, v in df[col].items()]
    return TrendSeries(metric=metric, unit=unit, range=range_, y_max=y_max, points=points)


def build_index(snap: energy.EnergySnapshot, station_type: str) -> EnergyIndex:
    """不可算时 score / level 为 None，前端显示「数据获取中」。docs/06 §十二"""
    r = snap.index
    return EnergyIndex(
        score=r.score if r else None,
        level=IndexLevel(r.level.value) if r else None,
        summary=energy.summary_text(r, station_type),
        estimated=snap.estimated,
        attribution=r.attribution if r else [],
    )


@dataclass(frozen=True)
class StationView:
    """一个站点的完整视图：home / detail / map 三个接口的公共部分。"""

    station: Station
    forecast: weather.Forecast
    snapshot: energy.EnergySnapshot
    summary: StationSummary
    index: EnergyIndex
    current: CurrentWeather | None


async def build_station_view(
    http: httpx.AsyncClient, station: Station, coord: Coord, db: AsyncSession | None = None
) -> StationView:
    fc = await weather.get_forecast(http, station.latitude, station.longitude)

    # pvlib 是 CPU 密集同步代码，丢进线程池，别卡事件循环。docs/05 §6.6
    loop = asyncio.get_running_loop()
    snap = await loop.run_in_executor(None, energy.compute, station, fc)

    # 累计与减排来自逐日累积表（定时任务维护）；没有记录时为 None
    total_kwh, co2_kg = (await accumulate.totals(db, station.id)) if db else (None, None)

    # 日发电与当前功率取自 snapshot：与指数、预测曲线同一条链路算出，不再单独跑一遍。
    # 目录容量口径待核验的站点不给绝对电量（指数照给，它与容量无关）
    daily = None if snap.blocked else snap.daily_kwh
    current = None if snap.blocked else snap.current_kw
    summary = to_summary(station, coord)
    grid = None if snap.blocked else snap.grid_kwh
    summary.metrics = StationMetrics(
        daily_generation=round(daily, 1) if daily is not None else None,
        current_power=round(current, 1) if current is not None else None,
        total_generation=round(total_kwh, 1) if total_kwh is not None else None,
        co2_reduction=round(co2_kg, 1) if co2_kg is not None else None,
        grid_generation=round(grid, 1) if grid is not None else None,
    )
    return StationView(
        station=station,
        forecast=fc,
        snapshot=snap,
        summary=summary,
        index=build_index(snap, station.type),
        current=build_current_weather(fc),
    )


async def default_station(db: AsyncSession, owner_id: str) -> Station | None:
    """默认展示容量排序第一座公开电站；空目录兼容历史个人数据。"""
    plant = (
        await db.execute(
            select(CatalogPlant)
            .where(CatalogPlant.status == "operating")
            .order_by(CatalogPlant.capacity_kw.desc(), CatalogPlant.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if plant is not None:
        return from_catalog(plant)
    q = select(Station).where(Station.owner_id == owner_id).order_by(Station.created_at)
    return (await db.execute(q)).scalars().first()


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
        station = await default_station(db, owner_id)
        if station is None:
            return HomeResponse(
                has_station=False, station=None, index=None, weather=None, trends=None, alert=None
            )

    v = await build_station_view(http, station, coord, db)
    # 首页也顺手扫一次预警，保证首次打开就有；常态刷新靠定时任务
    from app.weather_model import current_model

    if current_model.get() == "best_match":
        await alerts.scan_station(db, station, v.forecast)
    await db.commit()
    from app.services import prediction

    # 今日逐时出力已在 build_station_view 里算过，直接复用；明日单独算一遍供留档
    forecast_prediction = prediction.from_hourly(
        v.forecast,
        v.snapshot.hourly_kw,
        station,
        grid_kw=v.snapshot.grid_hourly_kw,
        curtailment_note=v.snapshot.curtailment_note,
    )
    from app.services import prediction_archive

    await asyncio.to_thread(prediction_archive.save, station, v.forecast, forecast_prediction)
    tomorrow = await asyncio.to_thread(prediction.compute, station, v.forecast, day_offset=1)
    await asyncio.to_thread(prediction_archive.save, station, v.forecast, tomorrow)
    return HomeResponse(
        prediction=forecast_prediction,
        has_station=True,
        station=v.summary,
        index=v.index,
        weather=v.current,
        trends=build_trend(v.forecast, TrendMetric.RADIATION),
        alert=await alerts.current_alert(db, station.id, v.forecast.tz),
    )


def outlook_hint(fc: weather.Forecast, index_score: float | None) -> str | None:
    """地图底部面板的一句话：未来 2 小时展望。规则模板，AI 接入后可替换。"""
    now_ts = fc.current_hour()
    rows = [fc.at(now_ts + pd.Timedelta(hours=h)) for h in range(0, 3)]
    rows = [r for r in rows if r is not None]
    if len(rows) < 2 or index_score is None:
        return None
    cloud_now = _num(rows[0].get("cloud_cover")) or 0.0
    cloud_later = _num(rows[-1].get("cloud_cover")) or 0.0
    base = "未来2小时整体适宜发电" if index_score >= 70 else "未来2小时发电条件一般"
    if cloud_later - cloud_now >= 20:
        at = (now_ts + pd.Timedelta(hours=1)).strftime("%H:%M")
        return f"{base}，{at}后云量逐步增加"
    if cloud_now - cloud_later >= 20:
        return f"{base}，云量逐步减少"
    return base
