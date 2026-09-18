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
- issue_outlooks         每日 08:30   我的电站三模式 7 天签发留档，供预报演变（docs/19 §二）
- prune_prediction        每日        按保留天数清理预测留档（docs/19 §二）
- model_resolution       每日一次    复核自动选择模型是否仍等于 ECMWF IFS（docs/17 §二）
- weather_upstream       每 5 分钟    探自建气象实例，熔断/恢复写日志（仅配了兜底时注册）
- warm_coords            每日一次    导出公开目录坐标给自建实例预热（同上，仅自建时注册）
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, id_pages
from app.db import pages as db_pages
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


def _orphan_alert_plants():
    """仍有生效预警、但不在个人站点表里的公开电站。

    首页顺手扫过它们才会留下预警，不接着扫的话只有再次被打开才会解除。
    用 NOT EXISTS 而不是把全部站点 id 拉进内存做差集 —— 后者又把表读回来了。
    """
    from app.models import Alert

    active = (
        select(Alert.station_id)
        .where(Alert.active.is_(True))
        .where(~select(Station.id).where(Station.id == Alert.station_id).exists())
        .distinct()
        .scalar_subquery()
    )
    return select(CatalogPlant).where(CatalogPlant.id.in_(active))


def _make_scan_alerts(app: FastAPI):
    """按站点串行扫描，只把「取站点」改成分批。

    不并发是有意的：`alerts.scan_station` 自带 per-station 锁并各自提交，
    跨站点本身独立，但这里整轮共用一个会话 —— AsyncSession 不能被多个任务同时用。
    真要并发得改成一任务一会话，而预警扫描每 15 分钟一轮、没有这个必要。
    """

    async def scan_one(db, station: Station) -> int:
        try:
            fc = await weather.station_forecast(app.state.http, station)
            # 扫描只用云图内容，URL 前缀由读接口按请求补
            sat = await satellite.load_scene_safely(app.state.http, station, "")
            return await alerts.scan_station(db, station, fc, sat)
        except Exception:  # noqa: BLE001  单站失败不能掀翻整轮
            log.exception("scan_alerts failed: station=%s", station.id)
            return 0

    async def job() -> None:
        from app.services.station import from_catalog

        size = settings.scan_batch_size
        seen = found = 0
        async with SessionLocal() as db:
            async for page in db_pages(db, select(Station), Station, size):
                for s in page:
                    found += await scan_one(db, s)
                    seen += 1
            async for plants in db_pages(db, _orphan_alert_plants(), CatalogPlant, size):
                for p in plants:
                    found += await scan_one(db, from_catalog(p))
                    seen += 1
        log.info("scan_alerts: %d detections over %d stations", found, seen)

    return job


def _make_issue_outlooks(app: FastAPI):
    """每天给我的电站签发一份三模式 7 天预测并留档。

    预报演变要的是「同一个目标日、历次起报」的序列，而留档此前只在用户打开页面时才写 ——
    没人打开的电站就没有历史。这个任务把签发变成每天固定一次，三个成员各存一份，
    演变按单一模型（`evolution_model`）串起来。docs/19 §二

    只读站点、只写文件，不写库：取完一页就放掉会话，之后的出网与计算不占连接。
    """

    async def one(station: Station) -> int:
        from app.services import ensemble
        from app.services.prediction_archive import save_outlook

        _, members = await ensemble.compute(app.state.http, station, settings.forecast_outlook_days)
        for m in members:
            await asyncio.to_thread(save_outlook, station, m.forecast, m.outlook)
        return len(members)

    async def job() -> None:
        sem = asyncio.Semaphore(settings.issue_outlooks_concurrency)

        async def guarded(station: Station) -> int:
            async with sem:
                return await one(station)

        stations_done = members_done = 0
        async for ids in id_pages(Station, settings.accumulate_batch_size):
            async with SessionLocal() as db:
                page = list(
                    (await db.execute(select(Station).where(Station.id.in_(ids)))).scalars().all()
                )
            results = await asyncio.gather(*(guarded(s) for s in page), return_exceptions=True)
            for station, r in zip(page, results, strict=True):
                if isinstance(r, BaseException):
                    # 单站失败不能掀翻整轮：明天还会再签发一次
                    log.error("issue_outlooks failed: station=%s", station.id, exc_info=r)
                    continue
                stations_done += 1
                members_done += r
        log.info("issue_outlooks: %d stations, %d archives", stations_done, members_done)

    return job


def _make_generate_reports(app: FastAPI):
    """一任务一会话地并发生成。

    这里不能用 accumulate 那种「算并发、写串行」：`generate_and_store` 从取数、
    算指数到写库全程用同一个会话，算与写分不开，而且它已经自己提交。所以改为每个
    任务开自己的会话。

    并发上限受数据库连接预算约束 —— 线上 `db_pool_size` + `db_max_overflow`
    一共 3 个连接，还要给请求流量留余量，所以按 `db_pool_size` 夹一次。
    翻页用 `id_pages`，每页取完就放掉连接，不跟工作任务抢。
    """

    async def one(station_id: str) -> bool:
        async with SessionLocal() as db:
            station = await db.get(Station, station_id)
            if station is None:  # 本轮跑到一半被删了
                return False
            await reports.generate_and_store(db, app.state.http, station)
            return True

    async def job() -> None:
        sem = asyncio.Semaphore(max(1, min(settings.reports_concurrency, settings.db_pool_size)))

        async def guarded(station_id: str) -> bool:
            async with sem:
                return await one(station_id)

        total = done = 0
        async for ids in id_pages(Station, settings.reports_batch_size):
            total += len(ids)
            results = await asyncio.gather(*(guarded(i) for i in ids), return_exceptions=True)
            for station_id, r in zip(ids, results, strict=True):
                if isinstance(r, BaseException):
                    log.error("generate_report failed: station=%s", station_id, exc_info=r)
                elif r:
                    done += 1
        log.info("generate_reports: %d/%d", done, total)

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

        from app.catalog import gem, importer, quality

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
                    report = await quality.apply_db(db)
                    await db.commit()
                log.info(
                    "sync_catalog %s: +%d ~%d dedup %d retired %d quality %s",
                    t,
                    res.added,
                    res.updated,
                    res.replaced,
                    retired,
                    report,
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
        # 逆地理编码只有腾讯位置服务这一条路（services/geo.reverse 拿不到就返回 None），
        # 没 key 时整轮必然一条都填不上 —— 和下面目录回填保持一致，直接不跑。
        if not settings.tencent_lbs_key:
            return
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


def start(app: FastAPI) -> AsyncIOScheduler | None:
    if not (
        settings.enable_scheduler
        or settings.map_scheduler_enabled
        or settings.open_meteo_fallback_base
    ):
        return None
    sched = AsyncIOScheduler(timezone="UTC")
    if settings.open_meteo_fallback_base:
        # 自建气象实例的健康探测。放在总开关之前：关了定时任务的实例（测试环境）
        # 也要能看出主源挂了，否则只能等用户面报错才发现。
        # 熔断状态是进程内的，所以每个实例各探各的。
        from app.providers import weather_transport

        async def probe_weather():
            ok = await weather_transport.probe(app.state.http)
            log.log(
                logging.INFO if ok else logging.WARNING,
                "weather_upstream: %s",
                weather_transport.status(),
            )

        sched.add_job(
            probe_weather,
            "interval",
            minutes=5,
            id="weather_upstream",
            next_run_time=datetime.now(UTC) + timedelta(seconds=20),
            max_instances=1,
            coalesce=True,
        )
        # 给自建实例导出预热用的坐标清单。目录按月同步，一天一次足够。
        # 实例侧的拉取脚本见 deploy/open-meteo/warmup.py —— 预热必须在实例本机跑，
        # 从北京驱动要 13 小时（大响应只有 16–18 KB/s）。
        from app.jobs import warm_coords

        async def export_warm_coords():
            await warm_coords.export()

        sched.add_job(
            export_warm_coords,
            CronTrigger(hour=19, minute=40),  # UTC 19:40 = 北京 03:40，避开整轮与扫描
            id="warm_coords",
            next_run_time=datetime.now(UTC) + timedelta(seconds=60),
            max_instances=1,
            coalesce=True,
        )
    if settings.map_scheduler_enabled:
        from app.jobs import map_prepare

        async def prepare_map():
            await map_prepare.refresh(app.state.http)

        sched.add_job(
            prepare_map,
            "interval",
            minutes=10,
            id="map_prepare",
            next_run_time=datetime.now(UTC) + timedelta(seconds=10),
            max_instances=1,
            coalesce=True,
        )
    if not settings.enable_scheduler:
        sched.start()
        log.info("scheduler started: %s", [j.id for j in sched.get_jobs()])
        return sched
    # 每小时第 10 分钟：错开整点的气象数据更新，也错开 scan_alerts 的 5/20/35/50
    # ——两者都遍历全部站点，叠在同一分钟只会把 CPU 峰值堆起来（气象数据本就共享缓存）
    sched.add_job(
        _make_accumulate(app),
        CronTrigger(minute=10),
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
    # 我的电站每日签发：排在全目录轮次（0:15）之后，且落在单点缓存的新时段内。docs/19 §二
    sched.add_job(
        _make_issue_outlooks(app),
        CronTrigger(hour=(settings.issue_outlooks_hour - 8) % 24, minute=30),
        id="issue_outlooks",
        max_instances=1,
        coalesce=True,
    )

    async def prune_prediction() -> None:
        from app.services import evolution

        removed = await asyncio.to_thread(evolution.prune)
        if removed:
            log.info("prune_prediction: %d day folders", removed)

    sched.add_job(
        prune_prediction,
        CronTrigger(hour=21, minute=40),
        id="prune_prediction",
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
    sched.start()
    log.info("scheduler started: %s", [j.id for j in sched.get_jobs()])
    return sched
