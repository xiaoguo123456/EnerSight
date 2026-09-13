"""图层：bbox → 量化块 → 渲染（缓存）→ 图片 URL + bounds。docs/06 §7.2、docs/05 §6.4"""

import asyncio
import math
import time
from datetime import UTC, datetime, timedelta

import httpx

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
)
from app.services import satellite
from app.weather_model import current_model

# 渲染上限：气象网格 0.25°，再放大没有信息量。docs/05 §6.5
MAX_ZOOM = 8


def _bounds(block: g.Block, coord: Coord) -> Bounds:
    def pt(lat: float, lon: float) -> LatLng:
        if coord == Coord.GCJ02:
            lon, lat = wgs84_to_gcj02(lon, lat)
        return LatLng(latitude=lat, longitude=lon)

    # 角点分别转换；4° 块上 GCJ 偏移导致的形变约 0.1%，对气象场可接受。docs/05 §6.6
    return Bounds(sw=pt(block.lat0, block.lon0), ne=pt(block.lat1, block.lon1))


# 区间均值量：辐射是前一小时均值标在区间末，「当前」是包含此刻的那一格。docs/04 §二
# 其余图层（气温、风速、云量）是瞬时值，取当前整点。
_INTERVAL_MEAN_LAYERS = {LayerType.RADIATION.value}


def _current_hour_index(times: list[str], layer: str = "") -> int:
    """当前时刻在网格时间轴（UTC 整点）上的位置。

    网格包含次日数据；日末读取次日首格，缺测不以相邻时刻替代。
    """
    now = datetime.now(UTC)
    step = (
        (datetime.fromisoformat(times[1]) - datetime.fromisoformat(times[0]))
        if len(times) > 1
        else timedelta(minutes=15)
    )
    minutes = max(1, int(step.total_seconds() / 60))
    label = now.replace(minute=now.minute // minutes * minutes, second=0, microsecond=0)
    if layer in _INTERVAL_MEAN_LAYERS and now > label:
        label += step
    try:
        return times.index(label.strftime("%Y-%m-%dT%H:%M"))
    except ValueError as exc:
        from app.errors import DataUnavailable

        raise DataUnavailable() from exc


async def _ensure_tile(
    http: httpx.AsyncClient, layer: str, block: g.Block, base_url: str
) -> tuple[str, str, bool]:
    """返回 (url, observed_at)。已渲染的直接给路径。"""
    data = await g.fetch_block(http, block)
    hi = _current_hour_index(data.times, layer)
    time_key = data.times[hi].replace(":", "")
    path = tiles.tile_path(layer, f"{current_model.get()}_15m_{block.key}", time_key)
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
    if layer in (LayerType.TEMPERATURE, LayerType.WIND, LayerType.RADIATION):
        from app.services.hres_layer import build

        return await build(http, layer, bbox, coord, base_url)
    w, s, e, n = bbox
    span = max(4, math.ceil(max(e - w, n - s) / 4) * 4)
    blocks = g.blocks_for_bbox(w, s, e, n, span)  # 一屏最多几块，防止恶意 bbox 拉爆
    # 云图先整层试卫星实况：只要有一块拿不到就整层退回预报云量。
    # 卫星是亮度拉伸的相对强度、预报是云量百分比，两种量混在一个响应里，
    # 同一个图例解释不了，observed_at 也会一半是观测时刻一半是预报时刻。
    sat_tiles: list[tuple[str, str, bool]] = []
    if layer == LayerType.CLOUD and span <= 8:
        for b in blocks:
            tile = await _ensure_satellite_tile(http, b, base_url)
            if tile is None:
                sat_tiles = []
                break
            sat_tiles.append(tile)
    actual_cloud = bool(sat_tiles)
    # 串行拉块：并发多块会触发 Open-Meteo 限流；块级缓存后只有冷块才真的回源
    results = sat_tiles or [await _ensure_tile(http, layer.value, b, base_url) for b in blocks]

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
            title="云量预报" if layer == LayerType.CLOUD and not actual_cloud else scale.title,
            type="scale" if scale.labels is None else "gradient",
            stops=None if scale.labels else scale.stops,
            labels=list(scale.labels) if scale.labels else None,
            colors=scale.colors,
        ),
        # 色阶图配合风速矢量，由客户端绘制流动粒子。
        frames=[LayerFrame(images=images)],
        frame_interval_ms=None,
        stale=any(item[2] for item in results),
    )
