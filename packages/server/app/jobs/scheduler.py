"""定时任务。APScheduler 进程内，V1 不需要独立 worker。docs/05 §五

任务清单：
- accumulate_generation  每小时     逐日发电累积（docs/07 §3.1）
- scan_alerts            每 30 分钟  扫描预警规则（docs/07 §五）
后续加入：预渲染图层、预生成 AI 报告、扫描预警。
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Station
from app.services import accumulate, alerts, weather

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
                    n += await alerts.scan_station(db, s, fc)
                except Exception:  # noqa: BLE001
                    log.exception("scan_alerts failed: station=%s", s.id)
            await db.commit()
        log.info("scan_alerts: %d detections over %d stations", n, len(stations))

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
        CronTrigger(minute="10,40"),
        id="scan_alerts",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    log.info("scheduler started: %s", [j.id for j in sched.get_jobs()])
    return sched
