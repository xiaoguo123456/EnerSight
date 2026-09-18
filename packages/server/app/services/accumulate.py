"""逐日发电累积。

每小时跑一次：对每个站点算「今日」预测发电量并 upsert 当天记录。
一天内多次覆盖，日终那次即最终值。累计 = 所有记录求和。

每个站点的「今日」按其时区算，Forecast 已处理。

**算与写分离**：算可以并发（受 `accumulate_concurrency` 限），写必须串行 ——
AsyncSession 不能被多个任务同时用。分批查询 + 分批提交，避免全表进内存、
也避免跑到一半出错就整轮回滚。
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import pages, upsert_insert
from app.models import DailyGeneration, Station
from app.services import correction, energy, weather

log = logging.getLogger(__name__)


async def upsert_daily(
    db: AsyncSession,
    station_id: str,
    day: date,
    kwh: float,
    current_kw: float | None,
    curtailed_kwh: float | None = None,
    timezone: str = "Asia/Shanghai",
    calculation_version: str | None = None,
    weather_model: str | None = None,
) -> None:
    stmt = upsert_insert(db, DailyGeneration).values(
        station_id=station_id,
        day=day,
        kwh=kwh,
        current_kw=current_kw,
        curtailed_kwh=curtailed_kwh,
        source="forecast",
        timezone=timezone,
        calculation_version=calculation_version,
        weather_model=weather_model,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["station_id", "day"],
        set_={
            "kwh": kwh,
            "current_kw": current_kw,
            "curtailed_kwh": curtailed_kwh,
            "source": "forecast",
            "timezone": timezone,
            "calculation_version": calculation_version,
            "weather_model": weather_model,
            "updated_at": datetime.now(UTC).replace(tzinfo=None),
        },
        # 实测值不被推算覆盖
        where=DailyGeneration.source != "measured",
    )
    await db.execute(stmt)


@dataclass(frozen=True)
class Daily:
    """一座站点算完的当日记录。只带写入需要的值，不再持有 ORM 对象。"""

    station_id: str
    day: date
    kwh: float
    current_kw: float | None
    curtailed_kwh: float | None
    timezone: str = "Asia/Shanghai"
    calculation_version: str | None = None
    weather_model: str | None = None


async def compute_station(
    http: httpx.AsyncClient, station: Station, sem: asyncio.Semaphore, k: float | None = None
) -> Daily | None:
    """只算不写，可并发。不可算（气象缺测）时返回 None：宁可缺一天，也不把 0 累进总量。

    闸门同时管住在途的上游请求与线程池里的 pvlib 计算 —— 两者都不该随站点数线性膨胀。
    k 是这座电站的实测订正系数，累计与首页今日要乘同一个数。docs/19 §三
    """
    async with sem:
        fc = await weather.station_forecast(http, station)
        loop = asyncio.get_running_loop()
        snap = await loop.run_in_executor(None, energy.compute, station, fc)
        if k is not None:
            snap = correction.apply_snapshot(station, fc, snap, k)
    if snap.daily_kwh is None or snap.blocked:
        log.warning("accumulate skipped (%s): station=%s", snap.blocked or "缺测", station.id)
        return None
    from app.services.prediction_basis import calculation_version

    return Daily(
        timezone=fc.tz,
        calculation_version=calculation_version(fc.now().date()),
        weather_model=fc.model,
        station_id=station.id,
        day=fc.now().date(),
        kwh=round(snap.daily_kwh, 1),
        current_kw=round(snap.current_kw, 1) if snap.current_kw is not None else None,
        # kwh 记可发电量，限电损失另记一列；没有规则为 None，不写 0。docs/17 §四
        curtailed_kwh=(
            round(snap.daily_kwh - snap.grid_kwh, 1) if snap.grid_kwh is not None else None
        ),
    )


async def accumulate_all(db: AsyncSession, http: httpx.AsyncClient) -> int:
    """全部站点跑一遍，返回处理数。单站失败不影响其他站。

    每批算完就提交：攒到最后一次 commit 的话，跑到一半出错前面的全白跑。
    """
    sem = asyncio.Semaphore(settings.accumulate_concurrency)
    done = 0
    async for page in pages(db, select(Station), Station, settings.accumulate_batch_size):
        ids = [s.id for s in page]  # 提交后再读 ORM 属性要看 expire 配置，先取出来
        factors = await correction.lookup_many(db, page)
        rows = await asyncio.gather(
            *(compute_station(http, s, sem, factors.get(s.id)) for s in page),
            return_exceptions=True,
        )
        for station_id, row in zip(ids, rows, strict=True):
            if isinstance(row, BaseException):
                # 定时任务里单站失败只记日志，不能掀翻整批
                log.error("accumulate failed: station=%s", station_id, exc_info=row)
                continue
            if row is None:
                continue
            await upsert_daily(
                db,
                row.station_id,
                row.day,
                row.kwh,
                row.current_kw,
                row.curtailed_kwh,
                row.timezone,
                row.calculation_version,
                row.weather_model,
            )
            done += 1
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

    # 只读取全球各时区可能属于今日的日期，不把昨日最新记录冒充今日。
    now = datetime.now(UTC)
    latest_q = (
        select(DailyGeneration)
        .where(
            DailyGeneration.station_id.in_(station_ids),
            DailyGeneration.day >= now.date() - timedelta(days=1),
            DailyGeneration.day <= now.date() + timedelta(days=1),
        )
        .order_by(DailyGeneration.station_id, DailyGeneration.day.desc())
    )
    latest: dict[str, DailyGeneration] = {}
    for row in (await db.execute(latest_q)).scalars():
        if row.day == now.astimezone(ZoneInfo(row.timezone)).date():
            latest.setdefault(row.station_id, row)

    out: dict[str, dict] = {}
    for sid in station_ids:
        if sid not in totals:
            continue
        row = latest.get(sid)
        out[sid] = {
            "daily": row.kwh if row else None,
            "current": row.current_kw
            if row and 0 <= (now - row.updated_at.replace(tzinfo=UTC)).total_seconds() <= 3600
            else None,
            "total": float(totals[sid]),
            "grid": (
                round(row.kwh - row.curtailed_kwh, 1)
                if row and row.curtailed_kwh is not None
                else None
            ),
        }
    return out
