"""单站全日预测。逐时出力与首页指数、当前功率共用 services.energy 的同一条链路；
缺少任一必要时刻时不将缺测当作零发电。"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.config import settings
from app.models import Station
from app.schemas.prediction import GenerationPrediction, PowerPoint
from app.services import energy
from app.services.prediction_basis import version_for_day
from app.services.weather import Forecast
from app.weather_model import current_model


def assumptions(station: Station, version: str) -> list[str]:
    out = [
        "未计入限电、检修及故障影响",
        "光伏采用标准损耗与默认组件参数；风电采用通用功率曲线与场站损耗",
        "缺失设备参数采用默认值；非实际并网电量",
        (
            f"光伏容配比假设 {settings.pv_dc_ac_ratio:g}，"
            f"系统损耗 {settings.pv_losses:.0%}（含逆变器），交流出力按额定容量限幅；非实测"
            if station.type == "solar"
            else "轮毂高度缺失时使用默认高度，各层风速按对数廓线插值，通用功率曲线未校准至实际机型"
        ),
        f"计算版本 {version}",
        "逐时曲线标注区间起点；风电按整点样本代表该小时作积分近似"
        if version == "model-v4"
        else "旧版逐时标签口径",
    ]
    blocked = getattr(station, "_prediction_blocked", None)
    if blocked:
        out.append(blocked)
    return out


def from_hourly(
    fc: Forecast, hourly_kw: pd.Series, station: Station, model: str | None = None
) -> GenerationPrediction:
    """由已算好的逐时出力组装响应。任一小时缺测则电量为 null；
    容量口径待核验的目录站点整条曲线为 null，原因写进 assumptions。"""
    if getattr(station, "_prediction_blocked", None):
        hourly_kw = pd.Series(np.nan, index=hourly_kw.index)
    version = version_for_day(pd.Timestamp(hourly_kw.index[0]).date())
    if station.type == "solar" and version == "model-v4":
        hourly_kw = hourly_kw.copy()
        hourly_kw.index = hourly_kw.index - pd.Timedelta(hours=1)
    values = hourly_kw.to_numpy(dtype=float)
    valid = bool(np.isfinite(values).all())
    return GenerationPrediction(
        model=model or current_model.get(),
        date=pd.Timestamp(hourly_kw.index[0]).date().isoformat(),
        timezone=fc.tz,
        generated_at=datetime.now(UTC).isoformat(),
        energy_kwh=round(float(values.sum()), 2) if valid else None,
        power_kw=[
            PowerPoint(time=t.isoformat(), value=round(float(v), 3) if np.isfinite(v) else None)
            for t, v in hourly_kw.items()
        ],
        assumptions=assumptions(station, version),
    )


def compute(
    station: Station, fc: Forecast, model: str | None = None, *, day_offset: int = 0
) -> GenerationPrediction:
    prep = energy.prepare(station, fc, day_offset=day_offset)
    return from_hourly(fc, energy.hourly_power(station, prep, fc.tz), station, model)
