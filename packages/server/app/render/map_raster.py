"""气象栅格模块：后台 COG 成果、固定 XYZ 瓦片与微信覆盖层适配。

在线渲染仅访问本地 COG，不下载原始模型；每张瓦片按墨卡托像素中心采样。
GCJ 成果是显示专用的经纬网格，不能用于气象计算或作为真实 EPSG:4326 对外交换。
"""

import fcntl
import io
import json
import math
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.shutil import copy as raster_copy
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from scipy.ndimage import map_coordinates

from app.geo.gcj02 import gcj02_to_wgs84_array
from app.render import hres
from app.render.colormap import SCALES

VERSION = "hres-v3"
COVERAGE = (60.0, 0.0, 150.0, 65.0)
MAX_TILES = 12
PIXELS = 256
MAX_ZOOM = 8


@dataclass(frozen=True)
class Region:
    lon0: float = COVERAGE[0]
    lat0: float = COVERAGE[1]
    lon1: float = COVERAGE[2]
    lat1: float = COVERAGE[3]
    key: str = "china-surroundings-v3"


def root(data_dir: str) -> Path:
    return Path(data_dir) / "map-rasters-v3"


def stamp(value: str | datetime) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M")


def xyz_bounds(z, x, y):
    size = 2**z
    west, east = x / size * 360 - 180, (x + 1) / size * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / size))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / size))))
    return west, south, east, north


def tile_indices(bbox, z):
    w, s, e, n = bbox
    size = 2**z

    def y(lat):
        lat = max(-85.051128, min(85.051128, lat))
        return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size

    x0 = max(0, math.floor((w + 180) / 360 * size))
    x1 = min(size - 1, math.ceil((e + 180) / 360 * size) - 1)
    y0, y1 = max(0, math.floor(y(n))), min(size - 1, math.ceil(y(s)) - 1)
    return [(z, x, yy) for x in range(x0, x1 + 1) for yy in range(y0, y1 + 1)]


def viewport_tiles(bbox):
    # 选择实际屏幕密度能承载的层级，而非相信客户端固定传来的 zoom=8。
    for z in range(MAX_ZOOM, -1, -1):
        result = tile_indices(bbox, z)
        if len(result) <= MAX_TILES:
            return result
    return [(0, 0, 0)]


def intersect(bbox):
    w, s, e, n = bbox
    w, s, e, n = max(w, COVERAGE[0]), max(s, COVERAGE[1]), min(e, COVERAGE[2]), min(n, COVERAGE[3])
    return (w, s, e, n) if w < e and s < n else None


def _write_cog(path, cube, transform):
    temp = path.with_suffix(f".{os.getpid()}.tif")
    raw = path.with_suffix(f".{os.getpid()}.raw.tif")
    try:
        with rasterio.open(
            raw,
            "w",
            driver="GTiff",
            width=cube.shape[2],
            height=cube.shape[1],
            count=len(cube),
            dtype="float32",
            crs="EPSG:4326",
            transform=transform,
            nodata=np.nan,
            tiled=True,
            blockxsize=256,
            blockysize=256,
        ) as dst:
            dst.write(cube)
        raster_copy(
            raw,
            temp,
            driver="COG",
            compress="DEFLATE",
            blocksize=256,
            overview_resampling="average",
            NUM_THREADS="1",
        )
        temp.replace(path)
    finally:
        raw.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)


def _prepare(data_dir, layer, meta, valid):
    """由受限后台进程调用；两套坐标成果全部完成后原子发布清单。"""
    directory = root(data_dir) / stamp(meta["reference_time"]) / stamp(valid)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / f"{layer}.json"
    if manifest.exists():
        try:
            for coord in ("wgs84", "gcj02"):
                with rasterio.open(directory / f"{layer}_{coord}.tif") as existing:
                    if existing.count != len(hres.FIELDS[layer]):
                        raise ValueError("栅格波段不完整")
            return str(manifest)
        except (OSError, ValueError, rasterio.errors.RasterioError):
            manifest.unlink(missing_ok=True)
    # 复用原生解码器。固定区域每个时效只读一次，后续浏览不再回源。
    from app.render import tiles

    tiles._TILE_DIR = Path(data_dir) / "tiles"
    frame = hres.read_frame(Region(), layer, meta, valid)
    cube = np.stack([np.flipud(frame.fields[k]) for k in hres.FIELDS[layer]])
    height, width = cube.shape[1:]
    dx = (COVERAGE[2] - COVERAGE[0]) / (width - 1)
    dy = (COVERAGE[3] - COVERAGE[1]) / (height - 1)
    # 解码器输出含边界的采样节点；GeoTIFF 的 transform 描述像素外边缘。
    transform = from_bounds(
        COVERAGE[0] - dx / 2,
        COVERAGE[1] - dy / 2,
        COVERAGE[2] + dx / 2,
        COVERAGE[3] + dy / 2,
        width,
        height,
    )
    _write_cog(directory / f"{layer}_wgs84.tif", cube, transform)
    lon, lat = np.meshgrid(np.linspace(60, 150, width), np.linspace(65, 0, height))
    wl, wt = gcj02_to_wgs84_array(lon, lat)
    positions = np.array([(65 - wt) / dy, (wl - 60) / dx])
    shifted = np.stack(
        [
            map_coordinates(band, positions, order=1, mode="constant", cval=np.nan, prefilter=False)
            for band in cube
        ]
    )
    _write_cog(directory / f"{layer}_gcj02.tif", shifted, transform)
    finite = cube[np.isfinite(cube)]
    if not finite.size:
        raise ValueError("区域气象栅格全部缺测")
    record = {
        "run_at": meta["reference_time"],
        "valid_at": valid.isoformat(),
        "layer": layer,
        "min": float(finite.min()),
        "max": float(finite.max()),
        "shape": list(cube.shape),
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    temp = manifest.with_suffix(".tmp")
    temp.write_text(json.dumps(record), encoding="utf-8")
    temp.replace(manifest)
    return str(manifest)


def prepare(data_dir, layer, meta, valid):
    """工作进程也持有文件锁：调度协程退出不会提前释放正在写入的成果。"""
    directory = root(data_dir) / stamp(meta["reference_time"]) / stamp(valid)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f".{layer}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare(data_dir, layer, meta, valid)


def available(data_dir, layer, target):
    candidates = []
    for path in root(data_dir).glob(f"*/*/{layer}.json"):
        try:
            record = json.loads(path.read_text())
            valid = datetime.fromisoformat(record["valid_at"])
            age = (target - valid).total_seconds()
            if 0 <= age <= 7200 and all(
                path.with_name(f"{layer}_{coord}.tif").exists() for coord in ("wgs84", "gcj02")
            ):
                candidates.append((valid, record["run_at"], path, record))
        except (OSError, ValueError, KeyError):
            continue
    if not candidates:
        return None
    _, _, path, record = max(candidates, key=lambda x: (x[0], x[1]))
    return path, record


def tile_path(data_dir, record, coord, z, x, y):
    return (
        Path(data_dir)
        / "tiles"
        / VERSION
        / stamp(record["run_at"])
        / stamp(record["valid_at"])
        / record["layer"]
        / coord
        / str(z)
        / str(x)
        / f"{y}.png"
    )


def render_tile(cog, output, layer, z, x, y):
    """GDAL 按窗口/概览读取；重投影后再着色，不把经纬度大图直接拉伸。"""
    if output.exists():
        return
    w, s, e, n = xyz_bounds(z, x, y)
    radius = 6378137
    bounds = (
        radius * math.radians(w),
        radius * math.asinh(math.tan(math.radians(s))),
        radius * math.radians(e),
        radius * math.asinh(math.tan(math.radians(n))),
    )
    with (
        rasterio.Env(GDAL_CACHEMAX=32 * 1024 * 1024, GDAL_NUM_THREADS="1"),
        rasterio.open(cog) as src,
    ):
        transform = from_bounds(*bounds, PIXELS, PIXELS)
        with WarpedVRT(
            src,
            crs="EPSG:3857",
            transform=transform,
            width=PIXELS,
            height=PIXELS,
            resampling=Resampling.bilinear,
            nodata=np.nan,
        ) as local:
            data = local.read()
    field = np.hypot(data[0], data[1]) if layer == "wind" else data[0]
    rgba = SCALES[layer].rgba(field)
    if layer == "temperature":
        # 固定 2℃ 等温边界，凸显局地变化；缺测边缘不画假等温线。
        valid = np.isfinite(field)
        bins = np.floor(np.where(valid, field, 0) / 2)
        edge = np.zeros(field.shape, dtype=bool)
        edge[1:] |= valid[1:] & valid[:-1] & (bins[1:] != bins[:-1])
        edge[:, 1:] |= valid[:, 1:] & valid[:, :-1] & (bins[:, 1:] != bins[:, :-1])
        rgba[edge, :3] = (rgba[edge, :3].astype(float) * 0.65).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(rgba).save(out, "PNG")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(out.getvalue())
    tmp.replace(output)


def render_view(data_dir, manifest, record, coord, bbox):
    """在线工作进程返回固定瓦片与少量标签/风矢量，不读取远端数据。"""
    cog = Path(manifest).with_name(f"{record['layer']}_{coord}.tif")
    images = []
    for z, x, y in viewport_tiles(bbox):
        path = tile_path(data_dir, record, coord, z, x, y)
        render_tile(cog, path, record["layer"], z, x, y)
        images.append((path.relative_to(Path(data_dir) / "tiles").as_posix(), xyz_bounds(z, x, y)))
    w, s, e, n = bbox
    count = 25 if record["layer"] == "wind" else 3
    points = [
        (lon, lat)
        for lat in np.linspace(s + (n - s) / 6, n - (n - s) / 6, count)
        for lon in np.linspace(w + (e - w) / 6, e - (e - w) / 6, count)
    ]
    if record["layer"] == "wind":
        points = [
            (lon, lat) for lat in np.linspace(s, n, count) for lon in np.linspace(w, e, count)
        ]
    with rasterio.open(cog) as src:
        samples = [
            (lon, lat, values.tolist())
            for (lon, lat), values in zip(points, src.sample(points), strict=True)
            if np.isfinite(values).all()
        ]
    return images, samples


def prewarm(data_dir, manifest):
    path = Path(manifest)
    record = json.loads(path.read_text())
    for coord in ("gcj02", "wgs84"):
        cog = path.with_name(f"{record['layer']}_{coord}.tif")
        for z in (3, 4, 5):
            for _, x, y in tile_indices(COVERAGE, z):
                render_tile(
                    cog, tile_path(data_dir, record, coord, z, x, y), record["layer"], z, x, y
                )


def prune(data_dir):
    # 按有效时刻清理，不能按起报批次删除仍在使用的较早模型预报。
    for directory in (root(data_dir), Path(data_dir) / "tiles" / VERSION):
        for run in directory.glob("*"):
            if not run.is_dir():
                continue
            for path in run.glob("*"):
                try:
                    when = datetime.strptime(path.name, "%Y%m%dT%H%M").replace(tzinfo=UTC)
                except ValueError:
                    continue
                if time.time() - when.timestamp() > 48 * 3600:
                    shutil.rmtree(path)
            if not any(run.iterdir()):
                run.rmdir()
