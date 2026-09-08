"""定时任务。APScheduler 进程内，V1 不需要独立 worker。docs/05 §五

任务清单：
- accumulate_generation  每小时     逐日发电累积（docs/07 §3.1）
- scan_alerts            每 30 分钟  扫描预警规则（docs/07 §五）
- generate_reports       每日 08:00  预生成 AI 报告（docs/08 §3.2）
- backfill_address       每小时     给缺地址的站点补逆地理编码
后续加入：预渲染图层、预生成 AI 报告、扫描预警。
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Station
from app.satellite import himawari
from app.services import accumulate, alerts, geo, reports, satellite, weather

log = logging.getLogger(__name__)


def _make_accumulate(app: FastAPI):
    async def job() -> None:
        async with SessionLocal() as db:
            n = await accumulate.accumulate_all(db, app.state.http)
        log.info("accumulate_generation: %d stations", n)

    return job


def _make_scan_alerts(app: FastAPI):
    async def job() -> None:
        async with SessionLocal() as db:
            stations = (await db.execute(select(Station))).scalars().all()
            n = 0
            for s in stations:
                try:
                    fc = await weather.get_forecast(app.state.http, s.latitude, s.longitude)
                    # 扫描只用云图内容，URL 前缀由读接口按请求补
                    sat = await satellite.load_scene_safely(app.state.http, s, "")
                    n += await alerts.scan_station(db, s, fc, sat)
                except Exception:  # noqa: BLE001
                    log.exception("scan_alerts failed: station=%s", s.id)
            await db.commit()
        log.info("scan_alerts: %d detections over %d stations", n, len(stations))

    return job


def _make_generate_reports(app: FastAPI):
    async def job() -> None:
        async with SessionLocal() as db:
            stations = (await db.execute(select(Station))).scalars().all()
            n = 0
            for s in stations:
                try:
                    await reports.generate_and_store(db, app.state.http, s)
                    n += 1
                except Exception:  # noqa: BLE001
                    log.exception("generate_report failed: station=%s", s.id)
        log.info("generate_reports: %d/%d", n, len(stations))

    return job


def _make_archive_cloud(app: FastAPI):
    """每 10 分钟归档各站点周边的分析波段瓦片；JMA 只留 35 小时，回放校准要自己攒。"""

    async def job() -> None:
        from app.satellite import archive
        from app.services import satellite as sat_svc

        async with SessionLocal() as db:
            stations = (await db.execute(select(Station))).scalars().all()
        try:
            latest = await himawari.latest_time(app.state.http)
        except Exception:  # noqa: BLE001
            log.warning("archive_cloud: satellite times unavailable")
            return
        written, seen = 0, set()
        for s in stations:
            bbox = sat_svc.station_bbox(s.latitude, s.longitude)
            band = sat_svc.analysis_band(s.latitude, s.longitude, latest)
            if (bbox, band) in seen:
                continue
            seen.add((bbox, band))
            try:
                written += await archive.archive_frame(app.state.http, latest, band, bbox)
            except Exception:  # noqa: BLE001
                log.warning("archive_cloud failed: station=%s", s.id, exc_info=True)
        removed = archive.prune()
        log.info("archive_cloud: %s, %d tiles written, %d days pruned", latest, written, removed)

    return job


def _make_backfill_address(app: FastAPI):
    async def job() -> None:
        async with SessionLocal() as db:
            rows = (
                (await db.execute(select(Station).where(Station.address.is_(None)))).scalars().all()
            )
            n = 0
            for s in rows:
                try:
                    r = await geo.reverse(app.state.http, s.latitude, s.longitude)
                    if r:
                        s.address = r.address
                        n += 1
                except Exception:  # noqa: BLE001
                    log.exception("backfill_address failed: station=%s", s.id)
            await db.commit()
        if rows:
            log.info("backfill_address: %d/%d", n, len(rows))

    return job


def start(app: FastAPI) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    # 每小时第 5 分钟，错开整点的气象数据更新
    sched.add_job(
        _make_accumulate(app),
        CronTrigger(minute=5),
        id="accumulate_generation",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        _make_scan_alerts(app),
        CronTrigger(minute="5,20,35,50"),  # 卫星 10 分钟一帧，短临外推要跟得上
        id="scan_alerts",
        max_instances=1,
        coalesce=True,
    )
    if settings.enable_archive:
        sched.add_job(
            _make_archive_cloud(app),
            CronTrigger(minute="2,12,22,32,42,52"),  # JMA 帧延迟约 10 分钟，错开整点
            id="archive_cloud",
            max_instances=1,
            coalesce=True,
        )
    # 每日预生成报告。站点时区暂按 UTC+8 处理，多时区站点后续按站点分组
    sched.add_job(
        _make_generate_reports(app),
        CronTrigger(hour=(settings.report_generate_hour - 8) % 24, minute=0),
        id="generate_reports",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        _make_backfill_address(app),
        CronTrigger(minute=20),
        id="backfill_address",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    log.info("scheduler started: %s", [j.id for j in sched.get_jobs()])
    return sched
