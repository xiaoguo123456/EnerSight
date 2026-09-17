"""导出公开目录的站点坐标清单，给自建气象实例做预热。

**为什么要预热**：自建实例的冷读成本是「每个新坐标」的 —— 实测单点全字段首次 5–6 秒、
同坐标重打 0.9–1.1 秒，邻近坐标沾不到热（0.1° 外仍 4.6 秒）。公开目录有一万多个唯一
坐标，而单点预报缓存只有进程内 512 槽，浏览公开电站几乎必冷。

**为什么预热要在实例本机跑**：北京→Buffalo 的大响应实测只有 16–18 KB/s（7.57 MB 的
已热批次要 421 秒），从北京驱动一万多个坐标要 13 小时，超过 6 小时的批次周期；
在实例本机走 localhost 是 0.9 秒/坐标（100 个一批），约 2.8 小时。所以这里只负责
**导出清单**，拉取由 `deploy/open-meteo/warmup.py` 在实例侧执行。
见 docs/2026-09-16-open-meteo-self-host.md。

**只导出公开目录**：自建站点是私有数据，不能落到公开静态目录；它们本来也被
scan_alerts 每 15 分钟拉热，不需要预热。
"""

import json
import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CatalogPlant
from app.render.tiles import tile_dir

log = logging.getLogger(__name__)

FILENAME = "warm-coords.json"


def path():
    """落在 /tiles 静态挂载下，实例侧按公网地址取，不新增接口与鉴权面。"""
    return tile_dir() / FILENAME


def usable(plant) -> bool:
    """省级占位坐标本来就不参与发电预测，预热它们是白花时间。

    判定与 `prediction_basis.catalog_basis` 同源（`app/catalog/quality.py` 写入的标记）。
    """
    location = (plant.provenance or {}).get("location") or {}
    return not (location.get("tier") == "province" and not location.get("method"))


async def export() -> int:
    """把公开目录的唯一坐标写成 JSON，返回坐标数。"""
    seen: set[tuple[float, float]] = set()
    skipped = 0
    async with SessionLocal() as db:
        rows = await db.stream_scalars(select(CatalogPlant))
        async for plant in rows:
            # latitude / longitude 在库里是 NOT NULL，不用再判空
            if not usable(plant):
                skipped += 1
                continue
            seen.add((plant.latitude, plant.longitude))
    target = path()
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        # 顺序稳定，实例侧按批切分的边界才稳定，日志能前后对照
        "coords": sorted([lat, lon] for lat, lon in seen),
    }
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, target)  # 原子替换，实例侧不会读到半个文件
    log.info(
        "warm_coords: %d 个唯一坐标，跳过省级占位 %d 座，%d 字节",
        len(seen),
        skipped,
        target.stat().st_size,
    )
    return len(seen)
