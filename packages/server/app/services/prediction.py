"""单站全日预测。逐时出力与首页指数、当前功率共用 services.energy 的同一条链路；
缺少任一必要时刻时不将缺测当作零发电。"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.models import Station
from app.schemas.prediction import GenerationPrediction, PowerPoint
from app.services import energy
from app.services.weather import Forecast
from app.weather_model import current_model

ASSUMPTIONS = [
    "未计入限电、检修及故障影响",
    "光伏采用标准损耗与默认组件参数、容配比按典型值；风电采用通用功率曲线与场站损耗",
    "缺失设备参数采用默认值；非实际并网电量",
]


def from_hourly(
    fc: Forecast, hourly_kw: pd.Series, model: str | None = None
) -> GenerationPrediction:
    """由已算好的逐时出力组装响应。任一小时缺测则电量为 null。"""
    values = hourly_kw.to_numpy(dtype=float)
    valid = bool(np.isfinite(values).all())
    return GenerationPrediction(
        model=model or current_model.get(),
        date=fc.current_hour().normalize().date().isoformat(),
        timezone=fc.tz,
        generated_at=datetime.now(UTC).isoformat(),
        energy_kwh=round(float(values.sum()), 2) if valid else None,
        power_kw=[
            PowerPoint(time=t.isoformat(), value=round(float(v), 3) if np.isfinite(v) else None)
            for t, v in hourly_kw.items()
        ],
        assumptions=list(ASSUMPTIONS),
    )


def compute(station: Station, fc: Forecast, model: str | None = None) -> GenerationPrediction:
    prep = energy.prepare(station, fc)
    return from_hourly(fc, energy.hourly_power(station, prep, fc.tz), model)
