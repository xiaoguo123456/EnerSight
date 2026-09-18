"""实测电量的记录、删除与重新拟合。docs/19 §三

只对我的电站；公开目录电站由平台维护，不能记。实测是用户经营数据，随删除电站一并删除。
"""

import asyncio
import calendar
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.errors import ApiError
from app.models import DailyGeneration, MeasuredEnergy, Station, StationCorrection
from app.schemas.measured import (
    CorrectionStatus,
    MeasuredEntry,
    MeasuredSummary,
    RecordMeasuredRequest,
)
from app.services import correction, hindcast

log = logging.getLogger(__name__)


def require_own(station: Station) -> None:
    if station.owner_id == correction.CATALOG_OWNER:
        raise ApiError("CATALOG_READ_ONLY", "公开电站由平台维护，不能记录实测电量", 403)


def _period(kind: str, raw: str, today: date) -> tuple[date, date]:
    """把 YYYY-MM-DD / YYYY-MM 换成起止日期，并挡掉还没过完、或早到没有模型值可比的。"""
    earliest = today - timedelta(days=settings.hindcast_max_days)
    try:
        if kind == "day":
            if len(raw) != 10:
                raise ValueError
            start = end = date.fromisoformat(raw)
        else:
            if len(raw) != 7:
                raise ValueError
            year, month = int(raw[:4]), int(raw[5:7])
            start = date(year, month, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])
    except ValueError:
        raise ApiError("INVALID_PARAM", f"日期格式不对：{raw}", 400) from None
    if end >= today:
        what = "这一天" if kind == "day" else "这个月"
        raise ApiError("INVALID_PARAM", f"{raw} {what}还没过完，过完再记", 400)
    if end < earliest:
        raise ApiError(
            "INVALID_PARAM",
            f"只能记最近 {settings.hindcast_max_days} 天内的电量，更早的没有模型同期值可比",
            400,
        )
    return start, end


def _label(e: MeasuredEnergy) -> str:
    return e.period_start.isoformat() if e.kind == "day" else e.period_start.isoformat()[:7]


async def _entries(db: AsyncSession, station_id: str) -> list[MeasuredEnergy]:
    # populate_existing：回算可能在另一个会话里刚写完，别拿本会话缓存的旧值
    rows = await db.execute(
        select(MeasuredEnergy)
        .where(MeasuredEnergy.station_id == station_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


_background: set[asyncio.Task] = set()


def _finished(task: asyncio.Task) -> None:
    _background.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.warning("measured refresh failed", exc_info=task.exception())


async def _refresh_bounded(db: AsyncSession, http: httpx.AsyncClient, station: Station) -> None:
    """记录 / 删除后立即回算并拟合，但最多等 measured_refresh_budget_s 秒。

    回算要出网，赶上上游慢或别的任务在抢，十几秒都有可能，而小程序请求 10 秒就超时。
    超时就先回「回算中」，回算在自己的会话里跑完；下次打开对账页就是新结果。
    """
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    station_id = station.id

    async def run() -> None:
        async with factory() as session:
            own = await session.get(Station, station_id)
            if own is not None:
                await refresh(session, http, own)

    task = asyncio.create_task(run())
    _background.add(task)
    task.add_done_callback(_finished)
    await asyncio.wait({task}, timeout=settings.measured_refresh_budget_s)


async def _write_daily(db: AsyncSession, station_id: str, e: MeasuredEnergy) -> None:
    """日电量写进逐日累积表，累计发电从此用实测；被覆盖的推算值存进记录，删除时恢复。"""
    row = (
        await db.execute(
            select(DailyGeneration).where(
                DailyGeneration.station_id == station_id,
                DailyGeneration.day == e.period_start,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            DailyGeneration(
                station_id=station_id,
                day=e.period_start,
                kwh=e.kwh,
                current_kw=None,
                curtailed_kwh=None,
                source="measured",
            )
        )
        return
    if row.source != "measured":
        e.prior_kwh = row.kwh
    row.kwh = e.kwh
    row.source = "measured"
    row.current_kw = None
    row.curtailed_kwh = None


async def _restore_daily(db: AsyncSession, station_id: str, e: MeasuredEnergy) -> None:
    row = (
        await db.execute(
            select(DailyGeneration).where(
                DailyGeneration.station_id == station_id,
                DailyGeneration.day == e.period_start,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.source != "measured":
        return
    if e.prior_kwh is None:
        await db.delete(row)
    else:
        row.kwh = e.prior_kwh
        row.source = "forecast"


async def record(
    db: AsyncSession, http: httpx.AsyncClient, station: Station, req: RecordMeasuredRequest
) -> MeasuredSummary:
    require_own(station)
    today = correction.today()
    existing = {(e.period_start, e.period_end): e for e in await _entries(db, station.id)}
    seen: set[tuple[date, date]] = set()
    for item in req.entries:
        start, end = _period(item.kind, item.date, today)
        if (start, end) in seen:
            raise ApiError("INVALID_PARAM", f"{item.date} 重复了", 400)
        seen.add((start, end))
        hours = ((end - start).days + 1) * 24
        if item.kwh > station.capacity_kw * hours * 1.1:
            raise ApiError(
                "INVALID_PARAM",
                f"{item.date} 的电量超过装机容量满发 {hours} 小时，请核对单位是不是 kWh",
                400,
            )
        e = existing.get((start, end))
        if e is None:
            e = MeasuredEnergy(
                station_id=station.id, kind=item.kind, period_start=start, period_end=end
            )
            db.add(e)
        e.kwh = item.kwh
        e.basis = req.basis
        e.recorded_at = datetime.now(UTC).replace(tzinfo=None)
        if item.kind == "day":
            await _write_daily(db, station.id, e)
    await db.commit()
    await _refresh_bounded(db, http, station)
    return await summary(db, station)


async def delete(
    db: AsyncSession, http: httpx.AsyncClient, station: Station, entry_id: int
) -> MeasuredSummary:
    require_own(station)
    e = await db.get(MeasuredEnergy, entry_id)
    if e is None or e.station_id != station.id:
        raise ApiError("NOT_FOUND", "这条记录不存在", 404)
    if e.kind == "day":
        await _restore_daily(db, station.id, e)
    await db.delete(e)
    await db.commit()
    await _refresh_bounded(db, http, station)
    return await summary(db, station)


async def refresh(db: AsyncSession, http: httpx.AsyncClient, station: Station) -> None:
    """补齐模型同期电量（缺的、或参数指纹变了的），再重新拟合。

    回算要出网；拿不到先不拦记录，模型值留空，交给每日任务补。
    """
    entries = await _entries(db, station.id)
    current = hindcast.digest(station)
    stale = [e for e in entries if e.model_digest != current or e.model_kwh is None]
    if stale:
        # 回算期间不占着数据库连接
        await db.commit()
        try:
            values = await hindcast.periods_kwh(
                http, station, [(e.period_start, e.period_end) for e in stale]
            )
        except Exception:  # noqa: BLE001  上游拿不到不拦记录，每日任务会补
            log.warning("hindcast failed: station=%s", station.id, exc_info=True)
            values = {}
        for e in stale:
            key = (e.period_start, e.period_end)
            if key not in values:
                continue
            e.model_kwh = values[key]
            # 算不出（缺测）时指纹留空，下次再试；算出了就记下指纹
            e.model_digest = current if values[key] is not None else None
    result = correction.fit(entries, correction.today())
    row = await db.get(StationCorrection, station.id)
    if row is None:
        row = StationCorrection(station_id=station.id)
        db.add(row)
    row.method = result.method
    row.sample_count = result.sample_count
    row.excluded_count = result.excluded_count
    row.k = result.k
    row.error_before = result.error_before
    row.error_after = result.error_after
    row.applied = result.applied
    row.reason = result.reason
    row.fitted_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()


async def summary(db: AsyncSession, station: Station) -> MeasuredSummary:
    entries = await _entries(db, station.id)
    result = correction.fit(entries, correction.today())
    row = await db.get(StationCorrection, station.id, populate_existing=True)
    enabled = station.correction_enabled is not False
    out: list[MeasuredEntry] = []
    for e in sorted(entries, key=lambda x: x.period_start, reverse=True):
        if e.id in result.used_ids:
            status = "used"
        elif e.id in result.excluded_ids:
            status = "excluded"
        elif e.model_kwh is None:
            status = "pending"
        else:
            status = "idle"
        out.append(
            MeasuredEntry(
                id=e.id,
                kind=e.kind,  # type: ignore[arg-type]
                date=_label(e),
                kwh=e.kwh,
                basis=e.basis,  # type: ignore[arg-type]
                model_kwh=e.model_kwh,
                ratio=round(e.kwh / e.model_kwh, 3) if e.model_kwh else None,
                status=status,  # type: ignore[arg-type]
                recorded_at=correction.local_iso(e.recorded_at),
            )
        )
    return MeasuredSummary(
        station_id=station.id,
        entries=out,
        correction=CorrectionStatus(
            enabled=enabled,
            applied=enabled and result.applied,
            k=result.k,
            method=result.method,  # type: ignore[arg-type]
            sample_count=result.sample_count,
            excluded_count=result.excluded_count,
            error_before=result.error_before,
            error_after=result.error_after,
            fitted_at=correction.local_iso(row.fitted_at) if row else None,
            reason=result.reason if enabled or not result.applied else "订正已关闭",
        ),
    )


async def refresh_all(db: AsyncSession, http: httpx.AsyncClient) -> int:
    """每日任务：有实测记录的电站逐个补模型值、按滚动窗口重拟合。返回处理的站数。"""
    ids = (await db.execute(select(MeasuredEnergy.station_id).distinct())).scalars().all()
    done = 0
    for sid in ids:
        station = await db.get(Station, sid)
        if station is None:
            continue
        try:
            await refresh(db, http, station)
            done += 1
        except Exception:  # noqa: BLE001  单站失败不能掀翻整轮
            await db.rollback()
            log.exception("fit_corrections failed: station=%s", sid)
    return done
