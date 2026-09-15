"""单站预测：今日一天与未来 7 天。逐时出力与首页指数、当前功率共用 services.energy 的
同一条链路；缺少任一必要时刻时不将缺测当作零发电。docs/16、docs/17 §二、§四"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.config import settings
from app.models import Station
from app.schemas.prediction import DailyOutlook, GenerationPrediction, PowerPoint, StationOutlook
from app.services import energy, province_grid
from app.services.prediction_basis import calculation_version, version_for_day
from app.services.weather import Forecast
from app.services.weather_text import describe
from app.weather_model import current_model

OUTLOOK_NOTES = [
    "七天点预报统一按 15 分钟计算，每个自然日 96 点",
    "15 分钟序列由上游按晴空指数从逐小时插值，不含新的气象信息；"
    "超短期滚动修正需实测与卫星，尚未提供",
    "起报时间以首日为准，后段可能由更早或更粗的批次补齐",
]


def assumptions(
    station: Station,
    version: str,
    curtailment_note: str | None = None,
    notes: tuple[str, ...] = (),
) -> list[str]:
    out = [
        f"可发电量未计入限电；预计上网电量{curtailment_note}，其他检修及故障影响未计入"
        if curtailment_note
        else "未计入限电、检修及故障影响",
        "光伏采用标准损耗与默认组件参数；风电采用机型功率曲线与场站损耗（公开电站按投运年份选机型）",
        "缺失设备参数采用默认值；非实际并网电量",
        (
            f"光伏容配比假设 {settings.pv_dc_ac_ratio:g}，"
            f"系统损耗 {settings.pv_losses:.0%}（含逆变器），交流出力按额定容量限幅；非实测"
            if station.type == "solar"
            else "轮毂高度缺失时使用默认高度，各层风速按对数廓线插值"
        ),
        *notes,
        f"计算版本 {version}",
        "曲线标注区间起点；风电按对应时刻样本代表该区间作积分近似"
        if version == "model-v4"
        else "旧版逐时标签口径",
    ]
    blocked = getattr(station, "_prediction_blocked", None)
    if blocked:
        out.append(blocked)
    return out


def _points(series: pd.Series) -> list[PowerPoint]:
    return [
        PowerPoint(time=t.isoformat(), value=round(float(v), 3) if np.isfinite(v) else None)
        for t, v in series.items()
    ]


def from_hourly(
    fc: Forecast,
    hourly_kw: pd.Series,
    station: Station,
    model: str | None = None,
    *,
    grid_kw: pd.Series | None = None,
    curtailment_note: str | None = None,
    step_minutes: int = 60,
    notes: tuple[str, ...] = (),
) -> GenerationPrediction:
    """由已算好的逐区间出力组装响应。任一区间缺测则电量为 null；
    容量口径待核验的目录站点整条曲线为 null，原因写进 assumptions。
    grid_kw 是计入出力约束后的上网出力，站点没有规则时为 None，响应里一律 null。"""
    if getattr(station, "_prediction_blocked", None):
        hourly_kw = pd.Series(np.nan, index=hourly_kw.index)
        grid_kw = None
    # 区间末标签 → 日期取首标签减一个 step，否则光伏 v4 首标签 00:15 仍在当日、但 24:00 会跨日
    first = pd.Timestamp(hourly_kw.index[0])
    day0 = first - pd.Timedelta(minutes=step_minutes) if station.type == "solar" else first
    version = version_for_day(day0.date())
    if station.type == "solar" and version == "model-v4":
        hourly_kw = hourly_kw.copy()
        hourly_kw.index = hourly_kw.index - pd.Timedelta(minutes=step_minutes)
        if grid_kw is not None:
            grid_kw = grid_kw.copy()
            grid_kw.index = grid_kw.index - pd.Timedelta(minutes=step_minutes)
    hours = step_minutes / 60.0
    values = hourly_kw.to_numpy(dtype=float)
    valid = bool(np.isfinite(values).all())
    grid_values = grid_kw.to_numpy(dtype=float) if grid_kw is not None else None
    grid_valid = valid and grid_values is not None and bool(np.isfinite(grid_values).all())
    energy_kwh = round(float(values.sum()) * hours, 2) if valid else None
    grid_energy = round(float(grid_values.sum()) * hours, 2) if grid_valid else None
    # 限电第二层：只有公开电站带统计区域；容量口径待核验时电量已置空，这里自然为 None
    province = province_grid.station_reference(
        energy_kwh, getattr(station, "_province_region", None), station.type, day0.date()
    )
    return GenerationPrediction(
        calculation_version=calculation_version(day0.date()),
        model=model or current_model.get(),
        date=pd.Timestamp(hourly_kw.index[0]).date().isoformat(),
        timezone=fc.tz,
        generated_at=datetime.now(UTC).isoformat(),
        energy_kwh=energy_kwh,
        power_kw=_points(hourly_kw),
        grid_energy_kwh=grid_energy,
        curtailed_kwh=(
            round(energy_kwh - grid_energy, 2)
            if energy_kwh is not None and grid_energy is not None
            else None
        ),
        grid_power_kw=_points(grid_kw) if grid_kw is not None else None,
        province_grid=province,
        basis=fc.basis(),
        resolution_minutes=step_minutes,
        assumptions=assumptions(station, version, curtailment_note, notes),
    )


def compute(
    station: Station,
    fc: Forecast,
    model: str | None = None,
    *,
    day_offset: int = 0,
    step_minutes: int | None = None,
) -> GenerationPrediction:
    """不算指数的轻量路径：全目录汇总与明日留档用。"""
    prep = energy.prepare(station, fc, day_offset=day_offset, step_minutes=step_minutes)
    hourly = energy.hourly_power(station, prep, fc.tz)
    grid = energy.grid_power(station, hourly, energy.target_day(fc, day_offset), prep.step_minutes)
    result = from_hourly(
        fc,
        hourly,
        station,
        model,
        grid_kw=grid,
        curtailment_note=energy.curtailment_note(station),
        step_minutes=prep.step_minutes,
        notes=energy.model_notes(station, fc, prep),
    )

    result.estimated = prep.estimated
    return result


def _daytime_weather(fc: Forecast, day: pd.Timestamp) -> str | None:
    """日间（06–18 整点瞬时值）众数天气。"""
    if "weather_code" not in fc.data:
        return None
    start = day.tz_localize(fc.tz) + pd.Timedelta(hours=6)
    seg = fc.data.loc[start : start + pd.Timedelta(hours=12)]["weather_code"].dropna()
    if seg.empty:
        return None
    return describe(int(seg.mode().iloc[0]))


def compute_days(
    station: Station, fc: Forecast, days: int, model: str | None = None
) -> StationOutlook:
    """未来 days 天，每天走 energy.compute 同一条链路，指数与电量一起给。docs/17 §二"""
    out: list[DailyOutlook] = []
    for k in range(days):
        step = fc.step_minutes
        snap = energy.compute(station, fc, day_offset=k, step_minutes=step)
        pred = from_hourly(
            fc,
            snap.hourly_kw,
            station,
            model,
            grid_kw=snap.grid_hourly_kw,
            curtailment_note=snap.curtailment_note,
            step_minutes=snap.step_minutes,
            notes=snap.notes,
        )
        finite = [p.value for p in pred.power_kw if p.value is not None]
        day = pd.Timestamp(pred.date)
        blocked = getattr(station, "_prediction_blocked", None)
        out.append(
            DailyOutlook(
                estimated=snap.estimated,
                date=pred.date,
                weekday=day.isoweekday(),
                energy_kwh=pred.energy_kwh,
                grid_energy_kwh=pred.grid_energy_kwh,
                curtailed_kwh=pred.curtailed_kwh,
                province_grid=pred.province_grid,
                index_score=snap.index.score if snap.index else None,
                index_level=snap.index.level if snap.index else None,
                weather_text=_daytime_weather(fc, day),
                peak_kw=round(max(finite), 3) if finite and pred.energy_kwh is not None else None,
                power_kw=pred.power_kw,
                grid_power_kw=pred.grid_power_kw,
                lead_days=k,
                resolution_minutes=pred.resolution_minutes,
            )
        )
    version = version_for_day(energy.target_day(fc, 0))
    notes = assumptions(station, version, energy.curtailment_note(station), snap.notes)
    notes += OUTLOOK_NOTES
    if blocked and blocked not in notes:
        notes.append(blocked)
    return StationOutlook(
        calculation_version=calculation_version(energy.target_day(fc, 0)),
        station_id=station.id,
        model=model or current_model.get(),
        timezone=fc.tz,
        generated_at=datetime.now(UTC).isoformat(),
        basis=fc.basis(),
        days=out,
        assumptions=notes,
    )
