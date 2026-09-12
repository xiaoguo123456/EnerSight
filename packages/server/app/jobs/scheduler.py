"""定时任务。APScheduler 进程内，V1 不需要独立 worker。docs/05 §五

任务清单：
- accumulate_generation  每小时      逐日发电累积（docs/07 §3.1）
- scan_alerts            每 15 分钟   扫描预警规则（docs/07 §五）
- archive_cloud          每 10 分钟   归档云图帧，含最近几帧回补（docs/07 §八）
- sync_catalog           每日检查     按 catalog_sync_days 间隔同步公开目录（docs/04 §七）
- generate_reports       每日 08:00   预生成 AI 报告（docs/08 §3.2）
- backfill_address       每小时      给缺地址的站点补逆地理编码
- fleet_prediction       每日 0/12 点 预热全目录汇总
- fleet_history          每 10 分钟   归档全目录日快照
- model_resolution       每日一次    复核自动选择模型是否仍等于 ECMWF IFS（docs/17 §二）
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import CatalogPlant, Station
from app.satellite import himawari
from app.services import accumulate, alerts, geo, reports, satellite, weather

log = logging.getLogger(__name__)


def _make_accumulate(app: FastAPI):
    async def job() -> None:
        async with SessionLocal() as db:
            n = await accumulate.accumulate_all(db, app.state.http)
        log.info("accumulate_generation: %d stations", n)

    return job


async def _stations_to_scan(db) -> list[Station]:
    """个人站点 + 首页顺手扫过、仍有生效预警的公开电站（否则它们的预警只有再次被打开才会解除）。"""
    from app.models import Alert
    from app.services.station import from_catalog

    stations = list((await db.execute(select(Station))).scalars().all())
    known = {s.id for s in stations}
    active_ids = (
        (await db.execute(select(Alert.station_id).where(Alert.active.is_(True)).distinct()))
        .scalars()
        .all()
    )
    pending = [sid for sid in active_ids if sid not in known]
    if pending:
        plants = (
            (await db.execute(select(CatalogPlant).where(CatalogPlant.id.in_(pending))))
            .scalars()
            .all()
        )
        stations.extend(from_catalog(p) for p in plants)
    return stations


def _make_scan_alerts(app: FastAPI):
    async def job() -> None:
        async with SessionLocal() as db:
            stations = await _stations_to_scan(db)
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
    """每 10 分钟归档各站点周边的分析波段瓦片；JMA 只留 35 小时，回放校准要自己攒。

    回补最近 `archive_backfill_frames` 帧，不只归档最新帧：最新帧的瓦片常常还没就绪
    （`targetTimes_fd.json` 先于瓦片更新），只取 latest 时这一帧会被永久跳过，
    35 小时后就再也补不回来。已落盘的瓦片不会重复出网，稳态下每轮只新增一帧。
    """

    async def job() -> None:
        from app.satellite import archive
        from app.services import satellite as sat_svc

        async with SessionLocal() as db:
            stations = (await db.execute(select(Station))).scalars().all()
        try:
            times = await himawari.available_times(app.state.http)
        except Exception:  # noqa: BLE001
            log.warning("archive_cloud: satellite times unavailable")
            return
        frames = archive.backfill_frames(times)
        written = missing = 0
        for when in frames:
            seen: set = set()
            for s in stations:
                bbox = sat_svc.station_bbox(s.latitude, s.longitude)
                # 波段按各站自身的太阳高度角定，与在线分析口径一致，回放才对得上
                band = sat_svc.analysis_band(s.latitude, s.longitude, when)
                if (bbox, band) in seen:
                    continue
                seen.add((bbox, band))
                try:
                    written += await archive.archive_frame(app.state.http, when, band, bbox)
                except Exception:  # noqa: BLE001  该帧未就绪，下一轮再补
                    missing += 1
                    log.debug("archive_cloud pending: %s %s", when, bbox, exc_info=True)
        removed = archive.prune()
        log.info(
            "archive_cloud: %d frames up to %s, %d tiles written, %d pending, %d days pruned",
            len(frames),
            frames[-1] if frames else None,
            written,
            missing,
            removed,
        )

    return job


def _make_sync_catalog(app: FastAPI):
    """按月从 GEM 拉光伏 / 风电追踪库重新导入目录；上次成功时间记在 data/catalog/.last_sync。"""

    async def job() -> None:
        from datetime import datetime, timedelta
        from pathlib import Path

        from app.catalog import gem, importer

        if not settings.gem_contact_email:
            return
        cat_dir = Path(settings.catalog_dir)
        stamp = cat_dir / ".last_sync"
        if stamp.exists():
            last = datetime.fromisoformat(stamp.read_text().strip())
            if datetime.now(UTC) - last < timedelta(days=settings.catalog_sync_days):
                return
        for t in ("solar", "wind"):
            try:
                d = await gem.download(app.state.http, t, cat_dir)
                rows = await asyncio.get_running_loop().run_in_executor(
                    None, importer.read_gem, d.path, "CHN"
                )
                async with SessionLocal() as db:
                    res = await importer.upsert(db, rows)
                    retired = await importer.retire_missing(db, "gem", t, res.seen_ids or set())
                    await db.commit()
                log.info(
                    "sync_catalog %s: +%d ~%d dedup %d retired %d",
                    t,
                    res.added,
                    res.updated,
                    res.replaced,
                    retired,
                )
            except Exception:  # noqa: BLE001
                log.exception("sync_catalog failed: %s", t)
                return  # 一类失败就不盖时间戳，下次再试
        cat_dir.mkdir(parents=True, exist_ok=True)
        stamp.write_text(datetime.now(UTC).isoformat())

    return job


def _make_backfill_address(app: FastAPI):
    """站点地址回填；顺带给公开电站目录回填省市区，每小时 100 条，省配额。"""

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
        if not settings.tencent_lbs_key:
            return
        async with SessionLocal() as db:
            plants = (
                (
                    await db.execute(
                        select(CatalogPlant).where(CatalogPlant.province.is_(None)).limit(100)
                    )
                )
                .scalars()
                .all()
            )
            done = 0
            for p in plants:
                try:
                    r = await geo.reverse(app.state.http, p.latitude, p.longitude)
                except Exception:  # noqa: BLE001
                    log.exception("backfill_catalog failed: plant=%s", p.id)
                    continue
                if r:
                    p.province, p.city, p.district = r.province, r.city, r.district
                    done += 1
                else:
                    p.province = ""  # 查不到也标记，避免每小时重复打同一批
            await db.commit()
        if plants:
            log.info("backfill_catalog: %d/%d", done, len(plants))

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
    # 目录同步：每天凌晨检查一次，实际按 catalog_sync_days 间隔执行
    sched.add_job(
        _make_sync_catalog(app),
        CronTrigger(hour=20, minute=30),  # UTC 20:30 = 北京 04:30
        id="sync_catalog",
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

    async def prepare_fleet():
        from app.services import fleet_prediction

        await fleet_prediction.ensure(app.state.http, "best_match")

    sched.add_job(
        prepare_fleet,
        CronTrigger(hour="0,12", minute=15),
        id="fleet_prediction",
        max_instances=1,
        coalesce=True,
    )
    from app.services import fleet_history

    sched.add_job(
        fleet_history.checkpoint,
        "interval",
        minutes=10,
        id="fleet_history",
        max_instances=1,
        coalesce=True,
    )
    from app.services import model_resolution

    async def check_model():
        await model_resolution.check(app.state.http)

    sched.add_job(
        check_model,
        CronTrigger(hour=1, minute=0),
        id="model_resolution",
        next_run_time=datetime.now(UTC) + timedelta(seconds=30),
        max_instances=1,
        coalesce=True,
    )
    from app.jobs import map_prepare

    async def prepare_map():
        await map_prepare.refresh(app.state.http)

    sched.add_job(
        prepare_map, "interval", minutes=10, id="map_prepare",
        next_run_time=datetime.now(UTC) + timedelta(seconds=10),
        max_instances=1, coalesce=True,
    )
    sched.start()
    log.info("scheduler started: %s", [j.id for j in sched.get_jobs()])
    return sched
