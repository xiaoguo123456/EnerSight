"""实测订正：拿用户记的电量拟合一个乘性系数，再把它作用到预测上。docs/19 §三

一个系数就能吃掉机型猜档、容量口径、系统损耗这几项最大的系统性偏差。只做乘性，不做加性 ——
加性订正在夜间会出负数或凭空出力。

**在逐时出力进入出力约束之前乘**，再按装机容量限幅，然后才算限电与上网；指数不乘（指数只反映气象）。
留档不乘：签发留档与预报演变存的是模型原始值，否则系数一变演变就跟着跳。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import MeasuredEnergy, Station, StationCorrection
from app.schemas.measured import CorrectionApplied
from app.services import energy
from app.services.energy import EnergySnapshot
from app.services.weather import Forecast

TZ = ZoneInfo("Asia/Shanghai")
CATALOG_OWNER = "__catalog__"


@dataclass(frozen=True)
class Fit:
    method: str | None  # day | month；样本不够时 None
    sample_count: int
    excluded_count: int
    k: float | None
    error_before: float | None  # 逐条平均误差（%）：模型与实测差多少
    error_after: float | None  # 留一法订正后的逐条平均误差（%）
    applied: bool
    reason: str | None
    used_ids: frozenset[int] = frozenset()
    excluded_ids: frozenset[int] = frozenset()


def _usable(e: MeasuredEnergy) -> bool:
    return e.model_kwh is not None and e.model_kwh > 0 and e.kwh > 0


def _in_range(e: MeasuredEnergy) -> bool:
    ratio = e.kwh / e.model_kwh  # type: ignore[operator]
    return settings.correction_ratio_min <= ratio <= settings.correction_ratio_max


def _mean_error(pairs: list[tuple[float, float]]) -> float:
    """逐条平均误差（%）：|预测 / 实测 − 1| 的平均。"""
    return round(sum(abs(m / y - 1.0) for m, y in pairs) / len(pairs) * 100.0, 1)


def _pool(entries: Iterable[MeasuredEnergy], kind: str, today: date) -> list[MeasuredEnergy]:
    days = (
        settings.correction_window_days if kind == "day" else settings.correction_window_months * 31
    )
    since = today - timedelta(days=days)
    return [e for e in entries if e.kind == kind and e.period_end >= since]


def _estimate(method: str, valid: list[MeasuredEnergy], excluded: int, skipped: list[int]) -> Fit:
    """留一法回测：每条都用其余记录拟出的系数去修它，比较修正前后的逐条平均误差。

    不按时间切最后 20% 做回测：日电量本身就有 ±10–20% 的起伏，回测段只有一两天时，
    碰上一天异常就能把整站订正否决掉，订正会今天开、明天关。留一法用上全部样本，
    问的是「这个系数对一般的一天有没有用」。单一乘性系数只有一个自由度，不怕用未来修过去。
    """
    ys = [e.kwh for e in valid]
    ms = [float(e.model_kwh) for e in valid]  # type: ignore[arg-type]
    total_y, total_m = sum(ys), sum(ms)
    before = _mean_error(list(zip(ms, ys, strict=True)))
    loo = [((total_y - y) / (total_m - m) * m, y) for m, y in zip(ms, ys, strict=True)]
    after = _mean_error(loo)
    k = total_y / total_m
    reason = None
    applied = True
    if not settings.correction_k_min <= k <= settings.correction_k_max:
        applied = False
        reason = (
            f"实测只有模型的 {k:.2f} 倍，差得太多，先核对装机容量与机型参数"
            if k < settings.correction_k_min
            else f"实测是模型的 {k:.2f} 倍，差得太多，先核对装机容量与机型参数"
        )
    elif after > before - settings.correction_min_gain_pct:
        applied = False
        reason = f"订正后逐日误差没有明显变小（{before:.0f}% → {after:.0f}%），暂不订正"
    return Fit(
        method=method,
        sample_count=len(valid),
        excluded_count=excluded,
        k=round(k, 4),
        error_before=before,
        error_after=after,
        applied=applied,
        reason=reason,
        used_ids=frozenset(e.id for e in valid),
        excluded_ids=frozenset(skipped),
    )


def fit(entries: Sequence[MeasuredEnergy], today: date) -> Fit:
    """日电量够就用日电量（最近 60 天，跟着季节滚动），否则用月电量（最近 6 个月）。"""
    counts: dict[str, int] = {}
    for method, need in (
        ("day", settings.correction_min_days),
        ("month", settings.correction_min_months),
    ):
        pool = _pool(entries, method, today)
        usable = [e for e in pool if _usable(e)]
        valid = [e for e in usable if _in_range(e)]
        skipped = [e.id for e in usable if not _in_range(e)]
        counts[method] = len(valid)
        if len(valid) >= need:
            return _estimate(method, valid, len(skipped), skipped)
    pending = sum(1 for e in entries if e.model_kwh is None)
    need_days = settings.correction_min_days - counts["day"]
    need_months = settings.correction_min_months - counts["month"]
    reason = f"再记 {need_days} 天日电量，或 {need_months} 个月月电量，就能开始订正"
    if pending:
        reason = f"{pending} 条记录的模型同期电量还在回算；" + reason
    all_skipped = [e.id for e in entries if _usable(e) and not _in_range(e)]
    return Fit(
        method=None,
        sample_count=0,
        excluded_count=len(all_skipped),
        k=None,
        error_before=None,
        error_after=None,
        applied=False,
        reason=reason,
        excluded_ids=frozenset(all_skipped),
    )


def today() -> date:
    return datetime.now(TZ).date()


def local_iso(value: datetime) -> str:
    """库里存的是不带时区的 UTC；对外按北京时间带偏移。"""
    return value.replace(tzinfo=UTC).astimezone(TZ).isoformat()


def _applied(row: StationCorrection | None) -> CorrectionApplied | None:
    if row is None or not row.applied or row.k is None or row.method is None:
        return None
    return CorrectionApplied(
        k=row.k,
        method=row.method,  # type: ignore[arg-type]
        sample_count=row.sample_count,
        error_before=row.error_before,
        error_after=row.error_after,
        fitted_at=local_iso(row.fitted_at),
    )


def _eligible(station: Station) -> bool:
    """只有落了库、归某个账号的自建电站才可能有实测。公开目录与临时构造的站点不查库。"""
    return (
        bool(station.owner_id)
        and station.owner_id != CATALOG_OWNER
        and station.correction_enabled is not False
    )


async def lookup(db: AsyncSession, station: Station) -> CorrectionApplied | None:
    """这座电站此刻该不该订正、按几倍。公开目录电站、关了开关、拟合未通过的，一律 None。"""
    if not _eligible(station):
        return None
    return _applied(await db.get(StationCorrection, station.id))


async def lookup_many(db: AsyncSession, stations: Sequence[Station]) -> dict[str, float]:
    """定时任务批量取系数：{station_id: k}，没有的站不在里面。"""
    ids = [s.id for s in stations if _eligible(s)]
    if not ids:
        return {}
    rows = (
        await db.execute(select(StationCorrection).where(StationCorrection.station_id.in_(ids)))
    ).scalars()
    return {r.station_id: r.k for r in rows if (a := _applied(r)) is not None and a.k}


def note(c: CorrectionApplied) -> str:
    """写进 assumptions 的一句话。"""
    unit = "天" if c.method == "day" else "个月"
    gap = (1.0 / c.k - 1.0) * 100.0
    side = "偏高" if gap > 0 else "偏低"
    text = f"已按 {c.sample_count} {unit}实测订正：模型{side} {abs(gap):.0f}%，按 {c.k:.2f} 倍修正"
    if c.error_before is not None and c.error_after is not None:
        text += f"（逐日误差 {c.error_before:.0f}% → {c.error_after:.0f}%）"
    return text


def scale(series: pd.Series, k: float, capacity_kw: float) -> pd.Series:
    """乘系数后按装机容量限幅。缺测的 NaN 原样保留，不当 0。"""
    return (series * k).clip(lower=0.0, upper=capacity_kw)


def apply_snapshot(
    station: Station, fc: Forecast, snap: EnergySnapshot, k: float, day_offset: int = 0
) -> EnergySnapshot:
    """在出力约束之前乘系数，再重算日电量、当前功率与上网口径。指数原样保留。"""
    if snap.daily_kwh is None and snap.current_kw is None:
        return snap
    cap = station.capacity_kw
    step = snap.step_minutes
    day = energy.target_day(fc, day_offset)
    hourly = scale(snap.hourly_kw, k, cap)
    grid = (
        energy.grid_power(station, hourly, day, step) if snap.grid_hourly_kw is not None else None
    )
    daily = float(hourly.sum()) * step / 60.0 if snap.daily_kwh is not None else None
    current = min(snap.current_kw * k, cap) if snap.current_kw is not None else None
    grid_current = None
    if grid is not None and current is not None:
        label = energy.current_label(fc, station.type)
        one = energy.grid_power(
            station, pd.Series([current], index=pd.DatetimeIndex([label])), day, step
        )
        grid_current = float(one.iloc[0]) if one is not None else None
    return replace(
        snap,
        daily_kwh=daily,
        current_kw=current,
        hourly_kw=hourly,
        grid_hourly_kw=grid,
        grid_kwh=float(grid.sum()) * step / 60.0 if grid is not None else None,
        grid_current_kw=grid_current,
    )
