"""图层：bbox → 量化块 → 渲染（缓存）→ 图片 URL + bounds。docs/06 §7.2、docs/05 §6.4"""

import asyncio
import math
import time
from datetime import UTC, datetime

import httpx
import numpy as np

from app.geo import wgs84_to_gcj02
from app.render import grid as g
from app.render import tiles
from app.render.colormap import SCALES
from app.satellite import himawari
from app.satellite.reproject import reproject
from app.schemas.common import Coord, LayerType
from app.schemas.layer import (
    Bounds,
    LatLng,
    LayerFrame,
    LayerImage,
    LayerResponse,
    Legend,
    ScalarSample,
    WindVector,
)
from app.services import satellite

# 渲染上限：气象网格 0.25°，再放大没有信息量。docs/05 §6.5
MAX_ZOOM = 8


def _bounds(block: g.Block, coord: Coord) -> Bounds:
    def pt(lat: float, lon: float) -> LatLng:
        if coord == Coord.GCJ02:
            lon, lat = wgs84_to_gcj02(lon, lat)
        return LatLng(latitude=lat, longitude=lon)

    # 角点分别转换；4° 块上 GCJ 偏移导致的形变约 0.1%，对气象场可接受。docs/05 §6.6
    return Bounds(sw=pt(block.lat0, block.lon0), ne=pt(block.lat1, block.lon1))


def _current_hour_index(times: list[str]) -> int:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:00")
    try:
        return times.index(now)
    except ValueError:
        return min(datetime.now(UTC).hour, len(times) - 1)


async def _ensure_tile(
    http: httpx.AsyncClient, layer: str, block: g.Block, base_url: str
) -> tuple[str, str, bool]:
    """返回 (url, observed_at)。已渲染的直接给路径。"""
    data = await g.fetch_block(http, block)
    hi = _current_hour_index(data.times)
    time_key = data.times[hi].replace(":", "")
    path = tiles.tile_path(layer, block.key, time_key)
    if not path.exists():
        loop = asyncio.get_running_loop()
        png = await loop.run_in_executor(None, tiles.render_png, data, layer, hi)
        tiles.write_tile(path, png)
    rel = path.relative_to(tiles.tile_dir()).as_posix()
    return (
        f"{base_url}/tiles/{rel}",
        data.times[hi] + "+00:00",
        time.time() - data.fetched_at > 3600,
    )


async def _ensure_satellite_tile(
    http: httpx.AsyncClient, block: g.Block, base_url: str
) -> tuple[str, str, bool] | None:
    """云图层用 Himawari 实况：白天可见光、夜间红外。docs/04 §4.1

    上游故障返回 None，调用方退回预报云量。
    """
    bbox = (block.lon0, block.lat0, block.lon1, block.lat1)
    lat_c, lon_c = (block.lat0 + block.lat1) / 2, (block.lon0 + block.lon1) / 2
    try:
        latest = await himawari.latest_time(http)
        band = satellite.analysis_band(lat_c, lon_c, latest)
        mosaic = await himawari.fetch_latest_mosaic(http, band, bbox)
    except Exception:  # noqa: BLE001
        return None
    time_key = f"{mosaic.observed_at.strftime('%Y%m%dT%H%M')}_{band}"
    path = tiles.tile_path("cloud-sat", block.key, time_key)
    if not path.exists():
        loop = asyncio.get_running_loop()
        rep = await loop.run_in_executor(None, reproject, mosaic, bbox, tiles.TILE_PX)
        lo, hi = satellite.CLOUD_RAMP[band]
        png = await loop.run_in_executor(None, tiles.render_cloud_png, rep.gray, lo, hi)
        tiles.write_tile(path, png)
    rel = path.relative_to(tiles.tile_dir()).as_posix()
    return f"{base_url}/tiles/{rel}", mosaic.observed_at.isoformat(timespec="minutes"), False


async def build_layer(
    http: httpx.AsyncClient,
    layer: LayerType,
    bbox: tuple[float, float, float, float],
    coord: Coord,
    base_url: str,
) -> LayerResponse:
    w, s, e, n = bbox
    span = max(4, math.ceil(max(e - w, n - s) / 4) * 4)
    blocks = g.blocks_for_bbox(w, s, e, n, span)  # 一屏最多几块，防止恶意 bbox 拉爆
    # 串行拉块：并发多块会触发 Open-Meteo 限流；块级缓存后只有冷块才真的回源
    results = []
    actual_cloud = []
    for b in blocks:
        sat = (
            await _ensure_satellite_tile(http, b, base_url)
            if layer == LayerType.CLOUD and span <= 8
            else None
        )
        actual_cloud.append(sat is not None)
        results.append(sat or await _ensure_tile(http, layer.value, b, base_url))

    vectors = []
    if layer == LayerType.WIND:
        for block in blocks:
            data = await g.fetch_block(http, block)
            hour = _current_hour_index(data.times)
            for i in range(g.N):
                for j in range(g.N):
                    speed = data.fields["wind"][hour, i, j]
                    direction = data.fields["wind_direction"][hour, i, j]
                    if not (np.isfinite(speed) and np.isfinite(direction)):
                        continue
                    lat = block.lat0 + i * (block.lat1 - block.lat0) / (g.N - 1)
                    lon = block.lon0 + j * (block.lon1 - block.lon0) / (g.N - 1)
                    if coord == Coord.GCJ02:
                        lon, lat = wgs84_to_gcj02(lon, lat)
                    angle = math.radians(float(direction))
                    vectors.append(
                        WindVector(
                            latitude=lat,
                            longitude=lon,
                            u=round(-float(speed) * math.sin(angle), 3),
                            v=round(-float(speed) * math.cos(angle), 3),
                        )
                    )
    samples = []
    if layer in (LayerType.TEMPERATURE, LayerType.RADIATION):
        for block in blocks:
            data = await g.fetch_block(http, block)
            hour = _current_hour_index(data.times)
            # 以视野内均匀的九个位置采样，双线性插值避免放大后无网格点可读。
            for fy in (1 / 6, 1 / 2, 5 / 6):
                for fx in (1 / 6, 1 / 2, 5 / 6):
                    lat, lon = s + (n - s) * fy, w + (e - w) * fx
                    if not (block.lat0 <= lat < block.lat1 and block.lon0 <= lon < block.lon1):
                        continue
                    y = (lat - block.lat0) / (block.lat1 - block.lat0) * (g.N - 1)
                    x = (lon - block.lon0) / (block.lon1 - block.lon0) * (g.N - 1)
                    i, j = min(int(y), g.N - 2), min(int(x), g.N - 2)
                    dy, dx = y - i, x - j
                    field = data.fields[layer.value][hour]
                    value = (
                        field[i, j] * (1 - dx) * (1 - dy)
                        + field[i, j + 1] * dx * (1 - dy)
                        + field[i + 1, j] * (1 - dx) * dy
                        + field[i + 1, j + 1] * dx * dy
                    )
                    if not np.isfinite(value):
                        continue
                    if coord == Coord.GCJ02:
                        lon, lat = wgs84_to_gcj02(lon, lat)
                    samples.append(
                        ScalarSample(latitude=lat, longitude=lon, value=round(float(value), 1))
                    )
    images = [
        LayerImage(url=url, bounds=_bounds(b, coord))
        for b, (url, _, _) in zip(blocks, results, strict=True)
    ]
    scale = SCALES[layer.value]
    return LayerResponse(
        layer=layer,
        observed_at=results[0][1] if results else datetime.now(UTC).isoformat(),
        unit=scale.unit,
        legend=Legend(
            title="云量预报" if layer == LayerType.CLOUD and not all(actual_cloud) else scale.title,
            type="scale" if scale.labels is None else "gradient",
            stops=None if scale.labels else scale.stops,
            labels=list(scale.labels) if scale.labels else None,
            colors=scale.colors,
        ),
        # 色阶图配合风速矢量，由客户端绘制流动粒子。
        frames=[LayerFrame(images=images)],
        frame_interval_ms=None,
        stale=any(item[2] for item in results),
        samples=samples,
        wind_vectors=vectors if layer == LayerType.WIND else None,
    )
