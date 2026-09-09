"""单站全日预测。缺少任一必要时刻时不将缺测当作零发电。"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.metrics import pv, solar, wind
from app.models import Station
from app.schemas.prediction import GenerationPrediction, PowerPoint
from app.services.weather import Forecast
from app.weather_model import current_model


def hourly_power(station: Station, frame: pd.DataFrame, tz: str) -> pd.Series:
    if station.type == "wind":
        height = station.hub_height or wind.default_hub_height(station.capacity_kw)
        return wind.power_curve(
            wind.extrapolate_wind(frame["wind_speed_10m"], height), station.capacity_kw
        )
    pos = solar.solar_position(
        station.latitude, station.longitude, tz, pd.DatetimeIndex(frame.index)
    )
    poa = pv.poa_from_components(
        tilt=station.tilt if station.tilt is not None else pv.default_tilt(station.latitude),
        azimuth=station.azimuth if station.azimuth is not None else 180,
        solar_zenith=pos["apparent_zenith"],
        solar_azimuth=pos["azimuth"],
        dni=frame["direct_normal_irradiance"],
        ghi=frame["shortwave_radiation"],
        dhi=frame["diffuse_radiation"],
    )
    return pv.dc_power(
        poa_global=poa,
        temp_air=frame["temperature_2m"],
        wind_speed=frame["wind_speed_10m"],
        capacity_kw=station.capacity_kw,
    ).clip(upper=station.capacity_kw)


def compute(station: Station, fc: Forecast, model: str | None = None) -> GenerationPrediction:
    today = fc.today()
    day = fc.current_hour().normalize()
    hours = pd.date_range(day, periods=24, freq="h")
    frame = today.reindex(hours)
    required = (
        ["wind_speed_10m"]
        if station.type == "wind"
        else [
            "wind_speed_10m",
            "temperature_2m",
            "shortwave_radiation",
            "direct_normal_irradiance",
            "diffuse_radiation",
        ]
    )
    complete = (
        np.isfinite(station.capacity_kw)
        and station.capacity_kw > 0
        and all(c in frame and np.isfinite(frame[c].astype(float)).all() for c in required)
    )
    values = hourly_power(station, frame, fc.tz) if complete else pd.Series(np.nan, index=hours)
    valid = bool(np.isfinite(values).all())
    return GenerationPrediction(
        model=model or current_model.get(),
        date=day.date().isoformat(),
        timezone=fc.tz,
        generated_at=datetime.now(UTC).isoformat(),
        energy_kwh=round(float(values.sum()), 2) if valid else None,
        power_kw=[
            PowerPoint(time=t.isoformat(), value=round(float(v), 3) if np.isfinite(v) else None)
            for t, v in values.items()
        ],
        assumptions=[
            "未计入限电、检修及故障影响",
            "光伏采用标准损耗与默认组件参数；风电采用通用功率曲线",
            "缺失设备参数采用默认值；非实际并网电量",
        ],
    )
