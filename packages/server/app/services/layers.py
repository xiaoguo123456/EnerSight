"""图层：bbox → 量化块 → 渲染（缓存）→ 图片 URL + bounds。docs/06 §7.2、docs/05 §6.4"""

import asyncio
from datetime import UTC, datetime

import httpx

from app.geo import wgs84_to_gcj02
from app.render import grid as g
from app.render import tiles
from app.render.colormap import SCALES
from app.satellite import himawari
from app.satellite.reproject import reproject
from app.schemas.common import Coord, LayerType
from app.schemas.layer import Bounds, LatLng, LayerFrame, LayerImage, LayerResponse, Legend
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
) -> tuple[str, str]:
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
    return f"{base_url}/tiles/{rel}", data.times[hi] + "+00:00"


async def _ensure_satellite_tile(
    http: httpx.AsyncClient, block: g.Block, base_url: str
) -> tuple[str, str] | None:
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
    return f"{base_url}/tiles/{rel}", mosaic.observed_at.isoformat(timespec="minutes")


async def build_layer(
    http: httpx.AsyncClient,
    layer: LayerType,
    bbox: tuple[float, float, float, float],
    coord: Coord,
    base_url: str,
) -> LayerResponse:
    w, s, e, n = bbox
    blocks = g.blocks_for_bbox(w, s, e, n)[:6]  # 一屏最多几块，防止恶意 bbox 拉爆
    # 串行拉块：并发多块会触发 Open-Meteo 限流；块级缓存后只有冷块才真的回源
    results = []
    for b in blocks:
        sat = await _ensure_satellite_tile(http, b, base_url) if layer == LayerType.CLOUD else None
        results.append(sat or await _ensure_tile(http, layer.value, b, base_url))

    images = [
        LayerImage(url=url, bounds=_bounds(b, coord))
        for b, (url, _) in zip(blocks, results, strict=True)
    ]
    scale = SCALES[layer.value]
    return LayerResponse(
        layer=layer,
        observed_at=results[0][1] if results else datetime.now(UTC).isoformat(),
        unit=scale.unit,
        legend=Legend(
            title=scale.title,
            type="scale" if scale.labels is None else "gradient",
            stops=None if scale.labels else scale.stops,
            labels=list(scale.labels) if scale.labels else None,
            colors=scale.colors,
        ),
        # V1 风场为静态色阶图，动画留到 V2。docs/05 §6.5
        frames=[LayerFrame(images=images)],
        frame_interval_ms=None,
    )
