"""三模式区间：ECMWF / ICON / GFS 各算一遍，给出电量区间与功率包络。docs/19 §一

**主数字取「中位成员」的完整结果，逐日独立选。** 不做逐点中位合成曲线：那条曲线求和
不等于任何一家的日电量，主数字与曲线会对不上；逐点取中位还会把不同成员的时序混在一起，
峰谷被削平，结果不是任何一个模式的预报。

模型是请求级 ContextVar（`app.weather_model.current_model`），一次只能算一个模式。
这里靠 asyncio 每个 Task 复制一份 context 来并发：task 内 set 只影响自己那份。
单点缓存本来就按模型隔离，三家各走各的缓存键，不会互相覆盖。
"""

import asyncio
import logging
from datetime import datetime
from statistics import median

import httpx

from app.config import settings
from app.models import Station
from app.schemas.common import SpreadLevel
from app.schemas.measured import CorrectionApplied
from app.schemas.prediction import (
    DailyOutlook,
    EnsembleSummary,
    ForecastBasis,
    MemberEnergy,
    PowerPoint,
    StationOutlook,
)
from app.services import prediction, weather
from app.weather_model import current_model

log = logging.getLogger(__name__)

MODEL = "ensemble"

NOTES = (
    "三模式区间：ECMWF、ICON、GFS 各算一遍；主数字与曲线取日电量居中的那家，逐日独立选",
    "区间是三家的分歧范围，不是概率区间，也不代表真实误差范围",
    "累计发电仍按自动模型逐小时累积，与本卡主数字可能差几个百分点",
)


def members() -> list[str]:
    """配置里的成员顺序即展示顺序。"""
    return [m.strip() for m in settings.ensemble_members.split(",") if m.strip()]


class Member:
    """一个成员算完的结果。

    outlook 是对外展示值（有实测订正时已订正），聚合用它；raw 是模型原始值，留档用它 ——
    系数一变演变就跟着跳，所以留档永远存未订正的。留档还要用各自的 forecast，一并带回。
    """

    __slots__ = ("model", "forecast", "outlook", "raw")

    def __init__(
        self,
        model: str,
        forecast: weather.Forecast,
        outlook: StationOutlook,
        raw: StationOutlook | None = None,
    ) -> None:
        self.model = model
        self.forecast = forecast
        self.outlook = outlook
        self.raw = raw if raw is not None else outlook


async def _member(
    http: httpx.AsyncClient,
    station: Station,
    days: int,
    model: str,
    correction: CorrectionApplied | None = None,
) -> Member:
    """在本 Task 自己的 context 里跑一个成员。订正在每家各自的逐时出力上做，再聚合。"""
    token = current_model.set(model)
    try:
        fc = await weather.station_forecast(http, station)
        raw, shown = await asyncio.to_thread(
            prediction.compute_days_pair, station, fc, days, model, correction
        )
        return Member(model, fc, shown, raw)
    finally:
        current_model.reset(token)


async def compute(
    http: httpx.AsyncClient,
    station: Station,
    days: int,
    correction: CorrectionApplied | None = None,
) -> tuple[StationOutlook, list[Member]]:
    """并发跑三家并聚合。返回 (聚合结果, 成功的成员)，成员的 raw 用于各自留档。

    某成员失败只记日志：剩两家仍给区间，剩一家退回单模型结果，全失败则抛出最后一个异常。
    """
    names = members()
    results = await asyncio.gather(
        *(_member(http, station, days, m, correction) for m in names), return_exceptions=True
    )
    ok: list[Member] = []
    failed: list[tuple[str, BaseException]] = []
    for name, r in zip(names, results, strict=True):
        if isinstance(r, BaseException):
            log.warning("ensemble member failed: station=%s model=%s", station.id, name, exc_info=r)
            failed.append((name, r))
        else:
            ok.append(r)
    if not ok:
        raise failed[-1][1]
    if len(ok) == 1:
        out = ok[0].outlook
        out.assumptions = [*out.assumptions, f"其余气象模式暂不可用，本次只用 {ok[0].model}"]
        return out, ok
    return aggregate(ok, [name for name, _ in failed], correction), ok


def _spread_level(percent: float | None) -> SpreadLevel | None:
    if percent is None:
        return None
    if percent < settings.ensemble_spread_moderate:
        return SpreadLevel.AGREE
    if percent < settings.ensemble_spread_high:
        return SpreadLevel.DIVERGE
    return SpreadLevel.STRONG


def _median_index(values: list[float]) -> int:
    """居中那个的下标。偶数个时取偏低的一个 —— 两家意见时不假装有中位数，宁可保守。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    return order[(len(order) - 1) // 2]


def _band(
    reference: list[PowerPoint], curves: list[list[PowerPoint]]
) -> tuple[list[PowerPoint], list[PowerPoint]]:
    """按参考曲线的时刻取各家最小 / 最大。

    时刻以中位成员为准：某家步长不同或缺这一刻就不参与该点，少于两家有值该点为 null
    （前端按 null 分段绘制）。带只用来画包络，不参与任何求和。
    """
    maps = [{p.time: p.value for p in c} for c in curves]
    low: list[PowerPoint] = []
    high: list[PowerPoint] = []
    for p in reference:
        values = [v for m in maps if (v := m.get(p.time)) is not None]
        if len(values) >= 2:
            low.append(PowerPoint(time=p.time, value=round(min(values), 3)))
            high.append(PowerPoint(time=p.time, value=round(max(values), 3)))
        else:
            low.append(PowerPoint(time=p.time, value=None))
            high.append(PowerPoint(time=p.time, value=None))
    return low, high


def _day_of(m: Member, date: str) -> DailyOutlook | None:
    return next((d for d in m.outlook.days if d.date == date), None)


def earliest_basis(ok: list[Member]) -> ForecastBasis | None:
    """三家起报时刻不一致时，对外统一报最早那一家的：区间里最旧的数据就是它。

    三家都每 6 小时起报一轮，但发布要等 3–7 小时，同一时刻常常是 ECMWF 还停在 08:00、
    ICON 已经出了 14:00。只报其中一家会让人以为三家是同一批；报最新的又夸大了新鲜度。
    任何一家拿不到起报，就说不清最早是哪一刻 —— 不猜，只给最早的拉取时刻。docs/17 §二
    """
    bases = [m.outlook.basis for m in ok if m.outlook.basis is not None]
    if not bases:
        return None
    if len(bases) < len(ok) or any(b.issued_at is None for b in bases):
        oldest = min(bases, key=lambda b: datetime.fromisoformat(b.fetched_at))
        return ForecastBasis(
            model=MODEL,
            resolved_model=None,
            issued_at=None,
            available_at=None,
            fetched_at=oldest.fetched_at,
        )
    first = min(bases, key=lambda b: datetime.fromisoformat(b.issued_at))
    return ForecastBasis(
        model=MODEL,
        resolved_model=None,
        issued_at=first.issued_at,
        available_at=first.available_at,
        fetched_at=first.fetched_at,
    )


def aggregate(
    ok: list[Member], failed: list[str], correction: CorrectionApplied | None = None
) -> StationOutlook:
    """以第一个成员的日期序列为准逐日聚合。"""
    base = ok[0]
    days: list[DailyOutlook] = []
    for slot in base.outlook.days:
        by_model = {m.model: _day_of(m, slot.date) for m in ok}
        present = [(model, d) for model, d in by_model.items() if d is not None]
        member_energy: list[MemberEnergy] = []
        for m in ok:
            d = by_model[m.model]
            member_energy.append(
                MemberEnergy(model=m.model, energy_kwh=d.energy_kwh if d is not None else None)
            )
        valued = [(model, d.energy_kwh, d) for model, d in present if d.energy_kwh is not None]
        if not valued:
            # 三家都算不出电量（缺测或口径待核验）：保留第一家的结构，区间留空
            day = slot.model_copy(deep=True)
            day.member_energy_kwh = member_energy
            days.append(day)
            continue
        energies = [e for _, e, _ in valued]
        median_model, _, median_day = valued[_median_index(energies)]
        day = median_day.model_copy(deep=True)
        low, high = min(energies), max(energies)
        mid = median(energies)
        day.energy_kwh_low = round(low, 2)
        day.energy_kwh_high = round(high, 2)
        day.member_energy_kwh = member_energy
        day.median_model = median_model
        day.power_kw_low, day.power_kw_high = _band(
            median_day.power_kw, [d.power_kw for _, d in present]
        )
        day.spread_percent = round((high - low) / mid * 100, 1) if mid else None
        day.spread_level = _spread_level(day.spread_percent)
        days.append(day)

    notes = [*base.outlook.assumptions, *NOTES]
    if failed:
        notes.append(f"本次未取到 {'、'.join(failed)}，区间由其余模式给出")
    return StationOutlook(
        calculation_version=base.outlook.calculation_version,
        station_id=base.outlook.station_id,
        model=MODEL,
        timezone=base.outlook.timezone,
        generated_at=base.outlook.generated_at,
        basis=earliest_basis(ok),
        days=days,
        correction=correction,
        ensemble=EnsembleSummary(
            members=[m.outlook.basis for m in ok if m.outlook.basis],
            spread_level=days[0].spread_level if days else None,
        ),
        assumptions=notes,
    )
