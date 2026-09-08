"""定时任务。APScheduler 进程内，V1 不需要独立 worker。docs/05 §五

任务清单：
- accumulate_generation  每小时     逐日发电累积（docs/07 §3.1）
后续加入：预渲染图层、预生成 AI 报告、扫描预警。
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.db import SessionLocal
from app.services import accumulate

log = logging.getLogger(__name__)


def _make_accumulate(app: FastAPI):
    async def job() -> None:
        async with SessionLocal() as db:
            n = await accumulate.accumulate_all(db, app.state.http)
        log.info("accumulate_generation: %d stations", n)

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
    sched.start()
    log.info("scheduler started: %s", [j.id for j in sched.get_jobs()])
    return sched
