"""Himawari 全圆盘拉取。NICT 实时真彩图，10 分钟一帧。docs/05 §6.7

瓦片是静止轨道投影，不是 Web Mercator，贴图前必须重投影（reproject.py）。
"""

import asyncio
import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
from PIL import Image

from app.cache import AsyncTTLCache
from app.config import settings
from app.errors import UpstreamUnavailable

TILE = 550
RETRIES = 3
CONCURRENCY = 3  # 经代理并发过高会偶发 TLS 拒连
_cache = AsyncTTLCache(maxsize=8, ttl_seconds=10 * 60)


@dataclass(frozen=True)
class FullDisk:
    observed_at: datetime  # UTC
    rgb: np.ndarray  # (H, W, 3) uint8

    @property
    def width(self) -> int:
        return self.rgb.shape[1]


async def _get(http: httpx.AsyncClient, url: str, timeout: float) -> httpx.Response:
    """带退避重试；经代理时偶发握手失败/超时，不重试会把整帧拖垮。"""
    for attempt in range(RETRIES):
        try:
            return await http.get(url, timeout=timeout)
        except httpx.HTTPError as exc:
            if attempt == RETRIES - 1:
                raise UpstreamUnavailable("卫星数据源暂时不可用") from exc
            await asyncio.sleep(0.5 * (attempt + 1))
    raise AssertionError("unreachable")


async def latest_time(http: httpx.AsyncClient) -> datetime:
    res = await _get(http, settings.himawari_latest, 15)
    try:
        res.raise_for_status()
        d = res.json()["date"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise UpstreamUnavailable("卫星数据源暂时不可用") from exc
    return datetime.strptime(d, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


async def _fetch_tile(
    http: httpx.AsyncClient, when: datetime, x: int, y: int, sem: asyncio.Semaphore
) -> np.ndarray:
    level = settings.himawari_level
    path = when.strftime("%Y/%m/%d/%H%M%S")
    url = f"{settings.himawari_base}/{level}d/{TILE}/{path}_{x}_{y}.png"
    async with sem:
        res = await _get(http, url, 30)
    if res.status_code != 200:
        # 全部 16 张（含太空角落）都应存在；缺了说明该帧还没上传完，不能拿黑图冒充夜间
        raise UpstreamUnavailable("卫星最新帧尚未就绪")
    return np.asarray(Image.open(io.BytesIO(res.content)).convert("RGB"))


async def _fetch_at(http: httpx.AsyncClient, when: datetime) -> FullDisk:
    key = f"himawari:{when.strftime('%Y%m%d%H%M')}"

    async def _load() -> FullDisk:
        n = settings.himawari_level
        sem = asyncio.Semaphore(CONCURRENCY)
        tasks = [_fetch_tile(http, when, x, y, sem) for y in range(n) for x in range(n)]
        tiles = await asyncio.gather(*tasks)
        rows = [np.concatenate(tiles[y * n : (y + 1) * n], axis=1) for y in range(n)]
        return FullDisk(observed_at=when, rgb=np.concatenate(rows, axis=0))

    return await _cache.get_or_load(key, _load)


async def fetch_full_disk(http: httpx.AsyncClient, when: datetime | None = None) -> FullDisk:
    """指定时刻的全圆盘；不指定取最新，最新帧不全则退回上一帧。"""
    if when is not None:
        return await _fetch_at(http, when)
    latest = await latest_time(http)
    try:
        return await _fetch_at(http, latest)
    except UpstreamUnavailable:
        return await _fetch_at(http, latest - timedelta(minutes=10))


async def fetch_previous(http: httpx.AsyncClient, when: datetime) -> FullDisk | None:
    """上一帧（10 分钟前），光流用。拿不到返回 None。"""
    try:
        return await fetch_full_disk(http, when - timedelta(minutes=10))
    except Exception:  # noqa: BLE001
        return None


def clear_cache() -> None:
    _cache.clear()
