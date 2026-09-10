"""同一应用容器内的地图工作进程，预处理和在线切片分别限为一个进程。"""

import asyncio
import fcntl
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta

from app.render import hres, map_raster, tiles

log = logging.getLogger(__name__)
_pools = {}
_gate = asyncio.Lock()
_render_gate = asyncio.Semaphore(2)


def data_dir():
    return str(tiles.tile_dir().parent.resolve())


async def run(fn, *args, background=False):
    key = "prepare" if background else "render"
    if key not in _pools:
        _pools[key] = ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
        )
    return await asyncio.get_running_loop().run_in_executor(_pools[key], fn, *args)


async def render(*args):
    # 限制提交到工作进程的队列，防止拖动/并发请求无限堆积。
    async with _render_gate:
        return await run(map_raster.render_view, *args)


async def refresh(http, hours=2):
    if _gate.locked():
        return
    async with _gate:
        directory = map_raster.root(data_dir())
        directory.mkdir(parents=True, exist_ok=True)
        # 共享数据卷文件锁：多个 API 进程启动时也只进行一份预处理。
        with (directory / ".prepare.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            try:
                meta = await hres.metadata(http)
                now = datetime.now(UTC)
                ready = []
                # 当前时效优先，随后准备未来两小时，跨整点不用等下载。
                for offset in range(hours + 1):
                    for layer in hres.FIELDS:
                        try:
                            valid = hres.valid_time(meta, layer, now + timedelta(hours=offset))
                            manifest = await run(
                                map_raster.prepare,
                                data_dir(),
                                layer,
                                meta,
                                valid,
                                background=True,
                            )
                            ready.append(manifest)
                        except Exception:
                            log.exception("地图预处理失败：%s +%sh", layer, offset)
                if not ready:
                    raise RuntimeError("当前气象栅格均未准备成功，请检查上游和预处理日志")
                # 成果已可服务，再补常用缩放层级缓存，避免预热阻塞其他图层就绪。
                for manifest in ready:
                    await run(map_raster.prewarm, data_dir(), manifest, background=True)
                await run(map_raster.prune, data_dir(), background=True)
                log.info("地图预处理完成：%s，%d 个图层时效", meta["reference_time"], len(ready))
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


async def shutdown():
    for pool in _pools.values():
        pool.shutdown(wait=False, cancel_futures=True)
    _pools.clear()
