"""Himawari-9 瓦片拉取：日本气象厅（JMA）官网的 Web Mercator XYZ 瓦片。docs/04、docs/05 §6.7

一帧 = 若干 256px JPEG 瓦片；按 bbox 只拉覆盖到的几张，按 (时刻, 波段, 瓦片) 缓存，
相邻站点与地图块共用。瓦片是标准 Web Mercator，贴地图不需要静止轨道重投影。
"""

import asyncio
import io
import math
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import numpy as np
from PIL import Image

from app.cache import AsyncTTLCache
from app.config import settings
from app.errors import UpstreamUnavailable

TILE = 256
RETRIES = 3
CONCURRENCY = 4

# 波段 → JMA 路径段。REP 是真彩合成，契约里归为 visible
BANDS: dict[str, str] = {
    "visible": "B03/ALBD",  # 可见光反照率，白天云量信号最干净
    "infrared": "B13/TBB",  # 红外亮温，全天可用，低暖云偏弱
    "vapor": "B08/TBB",
    "truecolor": "REP/ETC",
}

_tiles = AsyncTTLCache(maxsize=4096, ttl_seconds=60 * 60)
_times = AsyncTTLCache(maxsize=1, ttl_seconds=60)


@dataclass(frozen=True)
class Mosaic:
    observed_at: datetime  # UTC
    band: str
    rgb: np.ndarray  # (H, W, 3) uint8，Web Mercator
    zoom: int
    x0: int  # 左上瓦片
    y0: int

    @property
    def gray(self) -> np.ndarray:
        r, g, b = self.rgb[..., 0], self.rgb[..., 1], self.rgb[..., 2]
        return (0.299 * r + 0.587 * g + 0.114 * b).astype(np.uint8)


# ── Web Mercator 瓦片坐标 ──


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """连续瓦片坐标（整数部分是瓦片号，小数部分是瓦片内位置）"""
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(min(lat, 85.05), -85.05))
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    n = 2**zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


# ── 时刻 ──


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


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


async def available_times(http: httpx.AsyncClient) -> list[datetime]:
    """JMA 给出的全部可用时刻（约 35 小时），升序。"""

    async def _load() -> list[datetime]:
        res = await _get(http, f"{settings.himawari_base}/targetTimes_fd.json", 15)
        try:
            res.raise_for_status()
            items = res.json()
            times = sorted({_parse(i["validtime"]) for i in items})
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise UpstreamUnavailable("卫星数据源暂时不可用") from exc
        if not times:
            raise UpstreamUnavailable("卫星数据源暂无可用时刻")
        return times

    return await _times.get_or_load("times", _load)


async def latest_time(http: httpx.AsyncClient) -> datetime:
    return (await available_times(http))[-1]


async def previous_time(http: httpx.AsyncClient, when: datetime) -> datetime | None:
    """列表里 when 的前一帧；JMA 偶有缺帧，不能简单减 10 分钟。"""
    times = await available_times(http)
    earlier = [t for t in times if t < when]
    return earlier[-1] if earlier else None


# ── 瓦片 ──


def tile_url(when: datetime, band: str, x: int, y: int) -> str:
    zoom = settings.himawari_zoom
    ts = when.strftime("%Y%m%d%H%M%S")
    return f"{settings.himawari_base}/{ts}/fd/{ts}/{BANDS[band]}/{zoom}/{x}/{y}.jpg"


async def fetch_tile_bytes(
    http: httpx.AsyncClient, when: datetime, band: str, x: int, y: int, sem: asyncio.Semaphore
) -> bytes:
    """原始 JPEG 字节，按 (时刻, 波段, 瓦片) 缓存；归档直接落盘这份字节。"""
    zoom = settings.himawari_zoom
    key = f"{when.strftime('%Y%m%d%H%M%S')}:{band}:{zoom}:{x}:{y}"

    async def _load() -> bytes:
        async with sem:
            res = await _get(http, tile_url(when, band, x, y), 20)
        if res.status_code != 200:
            # 该时刻在列表里但瓦片还没出来（或圆盘外）：当未就绪，调用方退回上一帧
            raise UpstreamUnavailable("卫星最新帧尚未就绪")
        return res.content

    return await _tiles.get_or_load(key, _load)


def decode_tile(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


async def _fetch_tile(
    http: httpx.AsyncClient, when: datetime, band: str, x: int, y: int, sem: asyncio.Semaphore
) -> np.ndarray:
    return decode_tile(await fetch_tile_bytes(http, when, band, x, y, sem))


def tiles_for_bbox(bbox: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """覆盖 bbox 的瓦片范围 (x0, y0, x1, y1)，含端点。"""
    zoom = settings.himawari_zoom
    w, s, e, n = bbox
    x0, y0 = (int(v) for v in lonlat_to_tile(w, n, zoom))
    x1, y1 = (int(v) for v in lonlat_to_tile(e, s, zoom))
    limit = 2**zoom - 1
    return max(x0, 0), max(y0, 0), min(x1, limit), min(y1, limit)


async def fetch_mosaic(
    http: httpx.AsyncClient,
    when: datetime,
    band: str,
    bbox: tuple[float, float, float, float],
) -> Mosaic:
    """拼出覆盖 bbox（WGS84 w,s,e,n）的瓦片马赛克。"""
    zoom = settings.himawari_zoom
    x0, y0, x1, y1 = tiles_for_bbox(bbox)
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        _fetch_tile(http, when, band, x, y, sem)
        for y in range(y0, y1 + 1)
        for x in range(x0, x1 + 1)
    ]
    tiles = await asyncio.gather(*tasks)
    cols = x1 - x0 + 1
    rows = [np.concatenate(tiles[r * cols : (r + 1) * cols], axis=1) for r in range(y1 - y0 + 1)]
    return Mosaic(
        observed_at=when, band=band, rgb=np.concatenate(rows, axis=0), zoom=zoom, x0=x0, y0=y0
    )


async def fetch_latest_mosaic(
    http: httpx.AsyncClient, band: str, bbox: tuple[float, float, float, float]
) -> Mosaic:
    """最新一帧；最新帧瓦片未就绪则退回上一帧。"""
    latest = await latest_time(http)
    try:
        return await fetch_mosaic(http, latest, band, bbox)
    except UpstreamUnavailable:
        prev = await previous_time(http, latest)
        if prev is None:
            raise
        return await fetch_mosaic(http, prev, band, bbox)


def clear_cache() -> None:
    _tiles.clear()
    _times.clear()
