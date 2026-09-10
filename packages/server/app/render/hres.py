"""IFS HRES 原生 O1280 空间网格；不通过点预报 API 拼图。"""

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import fsspec
import httpx
import numpy as np
from omfiles import OmFileReader
from scipy.special import roots_legendre

from app.cache import AsyncTTLCache
from app.errors import DataUnavailable, UpstreamUnavailable
from app.render import tiles
from app.render.grid import Block

BASE = "https://openmeteo.s3.amazonaws.com/data_spatial/ecmwf_ifs"
FIELDS = {
    "temperature": ("temperature_2m",),
    "wind": ("wind_u_component_10m", "wind_v_component_10m"),
    "radiation": ("shortwave_radiation",),
}
_meta = AsyncTTLCache(maxsize=1, ttl_seconds=300)
_cache = AsyncTTLCache(maxsize=16, ttl_seconds=3600)
_gate = asyncio.Semaphore(2)


class RangeFS(fsspec.AbstractFileSystem):
    """兼容 OM 的 path 参数；持久连接、区块合并避免逐变量元数据重复回源。"""

    cachable = False

    def __init__(self):
        super().__init__()
        self.client = httpx.Client(timeout=30, follow_redirects=True)
        self.sizes = {}
        self.blocks = {}

    def info(self, path, **kwargs):
        if path not in self.sizes:
            r = self.client.head(path)
            r.raise_for_status()
            self.sizes[path] = int(r.headers["content-length"])
        return {"name": path, "size": self.sizes[path], "type": "file"}

    def cat_file(self, path, start=None, end=None, **kwargs):
        size = self.info(path)["size"]
        start, end = start or 0, min(end if end is not None else size, size)
        if start < 0:
            start += size
        # 元数据小块按 256 KiB 缓存；大数组读取保持连续 Range，不拉全世界文件。
        span = 262144
        a, b = start // span * span, min(size, math.ceil(end / span) * span)
        key = (path, a, b)
        if key not in self.blocks:
            r = self.client.get(path, headers={"Range": f"bytes={a}-{b - 1}"})
            r.raise_for_status()
            if (
                r.status_code != 206
                or len(r.content) != b - a
                or r.headers.get("content-range") != f"bytes {a}-{b - 1}/{size}"
            ):
                raise ValueError("上游未按 Range 返回网格字节")
            self.blocks[key] = r.content
        return self.blocks[key][start - a : end - a]

    def close(self):
        self.client.close()
        self.blocks.clear()


@lru_cache(maxsize=1)
def geometry():
    """ECMWF O1280：2560 条高斯纬线，南北对称，共 6599680 点。"""
    lat = np.degrees(np.arcsin(roots_legendre(2560)[0]))[::-1]
    row = np.arange(2560)
    counts = 20 + 4 * np.minimum(row, 2559 - row)
    offsets = np.r_[0, np.cumsum(counts)]
    return lat, counts, offsets


def regrid(values, first_row, last_row, lats, lons):
    """原生纬带 → 展示经纬度网格；U/V 分别插值，避免风向跨 360° 出错。"""
    native_lat, counts, offsets = geometry()
    rows = []
    base = offsets[first_row]
    for y in range(first_row, last_row + 1):
        row = values[offsets[y] - base : offsets[y + 1] - base]
        # 周期拼接 0° 经线；NaN 随插值传播，缺测不补零。
        x = np.arange(counts[y] + 1) * 360 / counts[y]
        rows.append(np.interp(lons % 360, x, np.r_[row, row[0]]))
    rows = np.asarray(rows)[::-1]
    source_lat = native_lat[first_row : last_row + 1][::-1]
    return np.stack(
        [np.interp(lats, source_lat, rows[:, x]) for x in range(len(lons))], axis=1
    ).astype(np.float32)


@dataclass
class Frame:
    block: Block
    run_at: str
    valid_at: str
    fields: dict[str, np.ndarray]
    fetched_at: float


async def metadata(http):
    async def load():
        try:
            r = await http.get(f"{BASE}/latest.json", timeout=20)
            r.raise_for_status()
            m = r.json()
            if not m.get("completed") or "O1280" not in m.get("crs_wkt", ""):
                raise ValueError("IFS HRES 网格或批次未就绪")
            return m
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise UpstreamUnavailable("IFS HRES 批次暂不可用，请稍后重试") from exc

    return await _meta.get_or_load("latest", load)


def valid_time(meta, layer, now=None):
    now = now or datetime.now(UTC)
    label = now.replace(minute=0, second=0, microsecond=0)
    if layer == "radiation" and now > label:
        label += timedelta(hours=1)
    times = [datetime.fromisoformat(t) for t in meta["valid_times"]]
    if label not in times or (
        layer == "radiation" and label <= datetime.fromisoformat(meta["reference_time"])
    ):
        raise DataUnavailable()
    return label


def read_frame(block, layer, meta, valid):
    run = datetime.fromisoformat(meta["reference_time"])
    key = f"{run:%Y%m%dT%H%M}_{valid:%Y%m%dT%H%M}_{layer}_{block.key}"
    directory = tiles.tile_dir().parent / "hres-grid-v1"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.npz"
    if path.exists():
        try:
            with np.load(path, allow_pickle=False) as z:
                fields = {k: z[k] for k in FIELDS[layer]}
                return Frame(
                    block, run.isoformat(), valid.isoformat(), fields, path.stat().st_mtime
                )
        except (OSError, ValueError, KeyError):
            path.unlink(missing_ok=True)
    native_lat, _, offsets = geometry()
    first = max(0, int(np.searchsorted(-native_lat, -block.lat1)) - 1)
    last = min(2559, int(np.searchsorted(-native_lat, -block.lat0)))
    # 展示采样约 0.07°；不把它宣称为源数据分辨率。
    lats = np.linspace(
        block.lat0, block.lat1, max(2, math.ceil((block.lat1 - block.lat0) / 0.07) + 1)
    )
    lons = np.linspace(
        block.lon0, block.lon1, max(2, math.ceil((block.lon1 - block.lon0) / 0.07) + 1)
    )
    url = f"{BASE}/{run:%Y/%m/%d/%H%MZ}/{valid:%Y-%m-%dT%H%M}.om"
    fs = RangeFS()
    fields = {}
    try:
        with OmFileReader.from_fsspec(fs, url) as root:
            for name in FIELDS[layer]:
                reader = root.get_child_by_name(name)
                if reader is None or tuple(reader.shape) != (1, 6599680):
                    raise ValueError("IFS HRES 变量维度异常")
                band = reader[:, int(offsets[first]) : int(offsets[last + 1])].reshape(-1)
                fields[name] = regrid(band, first, last, lats, lons)
    finally:
        fs.close()
    if not any(np.isfinite(v).any() for v in fields.values()):
        raise DataUnavailable()
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as f:
        np.savez_compressed(f, **fields)
    tmp.replace(path)
    for old in directory.glob("*.npz"):
        if time.time() - old.stat().st_mtime > 86400:
            old.unlink(missing_ok=True)
    return Frame(block, run.isoformat(), valid.isoformat(), fields, time.time())


async def fetch(block, layer, meta, valid):
    key = f"{meta['reference_time']}:{valid.isoformat()}:{layer}:{block.key}"

    async def load():
        async with _gate:
            try:
                return await asyncio.to_thread(read_frame, block, layer, meta, valid)
            except DataUnavailable:
                raise
            except Exception as exc:
                raise UpstreamUnavailable("IFS HRES 网格加载失败，请稍后重试") from exc

    return await _cache.get_or_load(key, load)
