"""站点能量指标：环境指数 + 发电估算。

把 Forecast 喂给 app/metrics 的物理模型。pvlib 是 CPU 密集同步代码，
调用方必须放进 executor，不要在 async 路由里直接调。docs/05 §6.6
"""

from dataclasses import dataclass

import pandas as pd

from app.metrics import pv, wind
from app.metrics.index import IndexResult, PvInputs, pv_index, wind_index
from app.models import Station
from app.services.weather import Forecast


@dataclass(frozen=True)
class EnergySnapshot:
    index: IndexResult
    daily_kwh: float
    current_kw: float | None


def _pv_inputs(station: Station, today: pd.DataFrame, tz: str) -> PvInputs:
    return PvInputs(
        latitude=station.latitude,
        longitude=station.longitude,
        tz=tz,
        capacity_kw=station.capacity_kw,
        tilt=station.tilt if station.tilt is not None else pv.default_tilt(station.latitude),
        azimuth=station.azimuth if station.azimuth is not None else 180.0,
        times=pd.DatetimeIndex(today.index),
        ghi=today["shortwave_radiation"].astype(float),
        dni=today["direct_normal_irradiance"].astype(float),
        dhi=today["diffuse_radiation"].astype(float),
        temp_air=today["temperature_2m"].astype(float),
        wind_speed=today["wind_speed_10m"].astype(float),
    )


def compute(station: Station, fc: Forecast) -> EnergySnapshot:
    """同步、CPU 密集。"""
    today = fc.today()
    now_ts = fc.current_hour()

    if station.type == "solar":
        inp = _pv_inputs(station, today, fc.tz)
        result = pv_index(inp)

        # 当前小时功率：用与指数同一条模型链算今日逐时，取当前点
        from app.metrics import solar

        pos = solar.solar_position(station.latitude, station.longitude, fc.tz, inp.times)
        poa = pv.poa_from_components(
            tilt=inp.tilt,
            azimuth=inp.azimuth,
            solar_zenith=pos["apparent_zenith"],
            solar_azimuth=pos["azimuth"],
            dni=inp.dni,
            ghi=inp.ghi,
            dhi=inp.dhi,
        )
        hourly_kw = pv.dc_power(
            poa_global=poa,
            temp_air=inp.temp_air,
            wind_speed=inp.wind_speed,
            capacity_kw=station.capacity_kw,
        )
        current = float(hourly_kw.get(now_ts, 0.0)) if now_ts in hourly_kw.index else None
        return EnergySnapshot(index=result, daily_kwh=result.actual_kwh, current_kw=current)

    # 风电：全天 24 小时，不分昼夜
    hub = (
        station.hub_height
        if station.hub_height is not None
        else wind.default_hub_height(station.capacity_kw)
    )
    v_hub = wind.extrapolate_wind(today["wind_speed_10m"].astype(float), hub)
    hourly_kw = wind.power_curve(v_hub, station.capacity_kw)
    daily = float(hourly_kw.sum())
    result = wind_index(daily, station.capacity_kw)
    current = float(hourly_kw.get(now_ts, 0.0)) if now_ts in hourly_kw.index else None
    return EnergySnapshot(index=result, daily_kwh=daily, current_kw=current)


def summary_text(result: IndexResult, station_type: str) -> str:
    """规则模板的一句话结论。AI 接入前的兜底，接入后也是降级路径。docs/08 §六"""
    s = result.score
    if s >= 85:
        base = "今日发电条件优秀"
    elif s >= 70:
        base = "今日适宜发电"
    elif s >= 55:
        base = "今日发电条件一般"
    else:
        base = "今日发电条件较差，建议关注"

    if station_type == "solar":
        rad = next((a for a in result.attribution if a.factor.value == "radiation"), None)
        if rad and rad.delta <= -25:
            return f"{base}，云层影响明显"
        if rad and rad.delta <= -10:
            return f"{base}，存在轻度云层影响"
    return base
