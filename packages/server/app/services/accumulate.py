"""逐日发电累积。

每小时跑一次：对每个站点算「今日」预测发电量并 upsert 当天记录。
一天内多次覆盖，日终那次即最终值。累计 = 所有记录求和。

每个站点的「今日」按其时区算，Forecast 已处理。
"""

import asyncio
import logging
from datetime import date

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import upsert_insert
from app.models import DailyGeneration, Station
from app.services import energy, weather

log = logging.getLogger(__name__)


async def upsert_daily(
    db: AsyncSession, station_id: str, day: date, kwh: float, current_kw: float | None
) -> None:
    stmt = upsert_insert(db, DailyGeneration).values(
        station_id=station_id, day=day, kwh=kwh, current_kw=current_kw, source="forecast"
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["station_id", "day"],
        set_={"kwh": kwh, "current_kw": current_kw, "source": "forecast"},
        # 实测值不被推算覆盖
        where=DailyGeneration.source != "measured",
    )
    await db.execute(stmt)


async def accumulate_station(db: AsyncSession, http: httpx.AsyncClient, station: Station) -> float:
    fc = await weather.get_forecast(http, station.latitude, station.longitude)
    loop = asyncio.get_running_loop()
    snap = await loop.run_in_executor(None, energy.compute, station, fc)
    today = fc.now().date()
    current = round(snap.current_kw, 1) if snap.current_kw is not None else None
    await upsert_daily(db, station.id, today, round(snap.daily_kwh, 1), current)
    return snap.daily_kwh


async def accumulate_all(db: AsyncSession, http: httpx.AsyncClient) -> int:
    """全部站点跑一遍，返回处理数。单站失败不影响其他站。"""
    stations = (await db.execute(select(Station))).scalars().all()
    done = 0
    for s in stations:
        try:
            await accumulate_station(db, http, s)
            done += 1
        except Exception:  # noqa: BLE001  定时任务里单站失败只记日志
            log.exception("accumulate failed: station=%s", s.id)
    await db.commit()
    return done


async def totals(db: AsyncSession, station_id: str) -> tuple[float | None, float | None]:
    """(累计发电 kWh, 累计减排 kg)。没有任何记录时返回 (None, None)。"""
    total = (
        await db.execute(
            select(func.sum(DailyGeneration.kwh)).where(DailyGeneration.station_id == station_id)
        )
    ).scalar()
    if total is None:
        return None, None
    return float(total), float(total) * settings.co2_factor_kg_per_kwh


async def metrics_from_db(db: AsyncSession, station_ids: list[str]) -> dict[str, dict]:
    """列表页用：一次查询拿到多个站点的 今日 / 即时 / 累计，不跑 pvlib。

    返回 {station_id: {daily, current, total}}，没有记录的站点不在结果里。
    """
    if not station_ids:
        return {}
    totals_q = (
        select(DailyGeneration.station_id, func.sum(DailyGeneration.kwh))
        .where(DailyGeneration.station_id.in_(station_ids))
        .group_by(DailyGeneration.station_id)
    )
    totals = dict((await db.execute(totals_q)).all())

    # 每站最新一天的记录（今日）
    latest_q = (
        select(DailyGeneration)
        .where(DailyGeneration.station_id.in_(station_ids))
        .order_by(DailyGeneration.station_id, DailyGeneration.day.desc())
    )
    latest: dict[str, DailyGeneration] = {}
    for row in (await db.execute(latest_q)).scalars():
        latest.setdefault(row.station_id, row)

    out: dict[str, dict] = {}
    for sid in station_ids:
        if sid not in totals:
            continue
        row = latest.get(sid)
        out[sid] = {
            "daily": row.kwh if row else None,
            "current": row.current_kw if row else None,
            "total": float(totals[sid]),
        }
    return out
