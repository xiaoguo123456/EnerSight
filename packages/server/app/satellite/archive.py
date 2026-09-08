"""云图帧归档：把站点周边的原始 JMA 瓦片按时刻落盘，供光流外推的历史回放校准。docs/07 §八

目录：{archive_dir}/{YYYYMMDD}/{HHMM}/{band}/{z}_{x}_{y}.jpg
瓦片级去重：相邻站点共用的瓦片只存一份。按天目录清理过期数据。
"""

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np

from app.config import settings
from app.satellite import himawari

log = logging.getLogger(__name__)


def archive_dir() -> Path:
    return Path(settings.archive_dir)


def tile_path(when: datetime, band: str, x: int, y: int) -> Path:
    when = when.astimezone(UTC)
    return (
        archive_dir()
        / when.strftime("%Y%m%d")
        / when.strftime("%H%M")
        / band
        / f"{settings.himawari_zoom}_{x}_{y}.jpg"
    )


async def archive_frame(
    http: httpx.AsyncClient, when: datetime, band: str, bbox: tuple[float, float, float, float]
) -> int:
    """归档一帧覆盖 bbox 的瓦片，返回新写入的张数。已存在的跳过，不重复出网。"""
    x0, y0, x1, y1 = himawari.tiles_for_bbox(bbox)
    sem = asyncio.Semaphore(himawari.CONCURRENCY)
    written = 0
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            path = tile_path(when, band, x, y)
            if path.exists():
                continue
            data = await himawari.fetch_tile_bytes(http, when, band, x, y, sem)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            written += 1
    return written


def load_mosaic(
    when: datetime, band: str, bbox: tuple[float, float, float, float]
) -> himawari.Mosaic | None:
    """从归档读一帧；任一瓦片缺失返回 None。与在线拉取的 Mosaic 结构一致，可直接重投影。"""
    x0, y0, x1, y1 = himawari.tiles_for_bbox(bbox)
    rows = []
    for y in range(y0, y1 + 1):
        row = []
        for x in range(x0, x1 + 1):
            path = tile_path(when, band, x, y)
            if not path.exists():
                return None
            row.append(himawari.decode_tile(path.read_bytes()))
        rows.append(np.concatenate(row, axis=1))
    return himawari.Mosaic(
        observed_at=when.astimezone(UTC),
        band=band,
        rgb=np.concatenate(rows, axis=0),
        zoom=settings.himawari_zoom,
        x0=x0,
        y0=y0,
    )


def archived_times(band: str, day: datetime) -> list[datetime]:
    """某天归档过的时刻（该波段任意瓦片存在即算）。"""
    day_dir = archive_dir() / day.astimezone(UTC).strftime("%Y%m%d")
    if not day_dir.exists():
        return []
    out = []
    for t in sorted(p.name for p in day_dir.iterdir() if p.is_dir()):
        if (day_dir / t / band).exists():
            out.append(datetime.strptime(day_dir.name + t, "%Y%m%d%H%M").replace(tzinfo=UTC))
    return out


def prune(retention_days: int | None = None) -> int:
    """删掉超过保留期的按天目录，返回删除的天数。"""
    days = retention_days if retention_days is not None else settings.archive_retention_days
    root = archive_dir()
    if not root.exists():
        return 0
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y%m%d")
    removed = 0
    for p in root.iterdir():
        if p.is_dir() and len(p.name) == 8 and p.name.isdigit() and p.name < cutoff:
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
    return removed
