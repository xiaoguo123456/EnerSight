"""卫星云图：JMA 瓦片 → 站点周边重投影 → 落盘 → URL；两帧光流出云团外推。docs/05 §6.7、docs/07 §四

白天：分析用可见光反照率（B03），展示用真彩（REP）；夜间两者都用红外（B13）。
红外看不到低暖云，夜间的云量与短临预警偏保守，这是物理限制。
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

from app.cache import AsyncTTLCache
from app.geo import wgs84_to_gcj02
from app.metrics import solar
from app.models import Station
from app.render import tiles
from app.satellite import himawari, motion
from app.satellite.reproject import Reprojected, reproject, to_png
from app.schemas.alert import CloudMotion
from app.schemas.common import Coord
from app.schemas.layer import Bounds, LatLng, LayerImage, Legend
from app.schemas.satellite import SatelliteCloudResponse

log = logging.getLogger(__name__)

HALF_SPAN_DEG = 2.5  # 站点周边 ±2.5°，约 500 km 见方，够看 2 小时外推
SIZE = 512
DAY_ELEVATION_DEG = 5.0  # 太阳高度角高于此算白天，可见光才有信号
MAX_FRAME_GAP = timedelta(minutes=25)  # 上一帧间隔超过此不做光流（JMA 偶有缺帧）

Band = Literal["visible", "infrared"]
# 云像素阈值（motion）与云图层透明度拉伸区间，按波段实测标定，见 docs/07 §四
CLOUD_THRESHOLD: dict[str, int] = {"visible": 100, "infrared": 48}
CLOUD_RAMP: dict[str, tuple[float, float]] = {"visible": (45.0, 200.0), "infrared": (35.0, 95.0)}
LEGEND = Legend(
    title="云量强度",
    type="gradient",
    stops=None,
    labels=["低", "高"],
    colors=["#1f2937", "#6b7280", "#ffffff"],
)
SceneStatus = Literal["ok", "unavailable"]


@dataclass(frozen=True)
class CloudScene:
    observed_at: datetime  # UTC
    band: Band  # 分析波段，也是响应里的 band
    now: Reprojected  # 分析波段当前帧
    prev: Reprojected | None  # 分析波段上一帧
    frame_minutes: float  # now 与 prev 的间隔
    image: Reprojected  # 展示帧（白天真彩，夜间红外）
    url: str


def station_bbox(lat: float, lon: float) -> tuple[float, float, float, float]:
    # 按 0.5° 对齐，相邻站点共用一张图
    w = np.floor((lon - HALF_SPAN_DEG) * 2) / 2
    s = np.floor((lat - HALF_SPAN_DEG) * 2) / 2
    return float(w), float(s), float(w + 2 * HALF_SPAN_DEG), float(s + 2 * HALF_SPAN_DEG)


def _bbox_key(bbox: tuple[float, float, float, float]) -> str:
    w, s, _, _ = bbox
    return f"{s:+06.1f}_{w:+07.1f}"


def is_day(lat: float, lon: float, when: datetime) -> bool:
    """按太阳高度角判昼夜，不看图像亮度（缺帧的黑图会误判）。"""
    pos = solar.solar_position(lat, lon, "UTC", pd.DatetimeIndex([when]))
    return float(pos["apparent_elevation"].iloc[0]) > DAY_ELEVATION_DEG


def analysis_band(lat: float, lon: float, when: datetime) -> Band:
    return "visible" if is_day(lat, lon, when) else "infrared"


async def _reproject(mosaic: himawari.Mosaic, bbox, size: int = SIZE) -> Reprojected:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, reproject, mosaic, bbox, size)


async def load_scene(
    http: httpx.AsyncClient, lat: float, lon: float, base_url: str, *, need_prev: bool
) -> CloudScene:
    """站点周边当前帧（及上一帧）。上游拿不到抛 UpstreamUnavailable。"""
    bbox = station_bbox(lat, lon)
    latest = await himawari.latest_time(http)
    band: Band = analysis_band(lat, lon, latest)
    now_m = await himawari.fetch_latest_mosaic(http, band, bbox)
    observed = now_m.observed_at
    display = "truecolor" if band == "visible" else "infrared"
    display_m = (
        now_m if display == band else await himawari.fetch_mosaic(http, observed, display, bbox)
    )
    now = await _reproject(now_m, bbox)
    image = now if display_m is now_m else await _reproject(display_m, bbox)
    if display == "infrared":
        image = stretch_infrared(image)

    time_key = f"{observed.strftime('%Y%m%dT%H%M')}_{display}"
    path = tiles.tile_path("satellite", _bbox_key(bbox), time_key)
    if not path.exists():
        tiles.write_tile(path, to_png(image.rgb))
    rel = path.relative_to(tiles.tile_dir()).as_posix()

    prev: Reprojected | None = None
    gap = motion.FRAME_MINUTES
    if need_prev:
        t_prev = await himawari.previous_time(http, observed)
        if t_prev is not None and observed - t_prev <= MAX_FRAME_GAP:
            try:
                prev = await _reproject(await himawari.fetch_mosaic(http, t_prev, band, bbox), bbox)
                gap = (observed - t_prev).total_seconds() / 60
            except himawari.UpstreamUnavailable:
                prev = None
    return CloudScene(observed, band, now, prev, gap, image, f"{base_url}/tiles/{rel}")


def stretch_infrared(rep: Reprojected) -> Reprojected:
    """JMA 红外亮温图对比度很低（晴空 ~40、云 ~120），展示前按 CLOUD_RAMP 拉伸成灰度图。"""
    lo, hi = CLOUD_RAMP["infrared"]
    g = (np.clip((rep.gray.astype(float) - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    return Reprojected(rgb=np.repeat(g[..., None], 3, axis=2), gray=rep.gray, bbox=rep.bbox)


def _latlng(lat: float, lon: float, coord: Coord) -> LatLng:
    if coord == Coord.GCJ02:
        lon, lat = wgs84_to_gcj02(lon, lat)
    return LatLng(latitude=lat, longitude=lon)


def to_response(
    scene: CloudScene, station: Station, tz: str, coord: Coord
) -> SatelliteCloudResponse:
    w, s, e, n = scene.now.bbox
    observed = scene.observed_at.astimezone(ZoneInfo(tz)).isoformat(timespec="minutes")
    return SatelliteCloudResponse(
        band=scene.band,
        observed_at=observed,
        image=LayerImage(
            url=scene.url, bounds=Bounds(sw=_latlng(s, w, coord), ne=_latlng(n, e, coord))
        ),
        legend=LEGEND,
        station_marker=_latlng(station.latitude, station.longitude, coord),
    )


def estimate_motion(scene: CloudScene, station: Station) -> motion.CloudMotionEstimate | None:
    if scene.prev is None:
        return None
    return motion.estimate(
        scene.prev.gray,
        scene.now.gray,
        scene.now.bbox,
        station.latitude,
        station.longitude,
        cloud_threshold=CLOUD_THRESHOLD[scene.band],
        frame_minutes=scene.frame_minutes,
    )


def local_time(observed_at: datetime, minutes: int, tz: str) -> str:
    return (observed_at + timedelta(minutes=minutes)).astimezone(ZoneInfo(tz)).strftime("%H:%M")


def to_cloud_motion(
    est: motion.CloudMotionEstimate, station: Station, observed_at: datetime, tz: str
) -> CloudMotion | None:
    """只有逼近中的云团才出三宫格；已在云下也算（距离 0）。"""
    if est.impact_minutes is None or est.distance_km is None:
        return None
    start = (observed_at + timedelta(minutes=est.impact_minutes)).astimezone(ZoneInfo(tz))
    return CloudMotion(
        distance_km=round(est.distance_km),
        direction=est.heading_text,
        direction_detail=f"自{est.origin_text}方向移来，约 {est.speed_kmh:.0f} km/h",
        impact_in_minutes=est.impact_minutes,
        impact_start_time=start.strftime("%H:%M"),
        reference_station=f"距{station.name}",
        observed_at=observed_at.isoformat(),
        impact_start_at=start.isoformat(),
    )


@dataclass(frozen=True)
class SceneResult:
    scene: CloudScene | None
    status: SceneStatus

    @property
    def known(self) -> bool:
        """是否拿到了确定的观测结论"""
        return self.status == "ok"


async def load_scene_safely(
    http: httpx.AsyncClient, station: Station, base_url: str
) -> SceneResult:
    """卫星是增强项：上游故障不让预报类预警跟着挂。"""
    try:
        scene = await load_scene(
            http, station.latitude, station.longitude, base_url, need_prev=True
        )
    except Exception:  # noqa: BLE001
        log.warning("satellite scene unavailable: station=%s", station.id, exc_info=True)
        return SceneResult(None, "unavailable")
    return SceneResult(scene, "ok")


async def get_cloud(
    http: httpx.AsyncClient, station: Station, tz: str, coord: Coord, base_url: str
) -> SatelliteCloudResponse:
    scene = await load_scene(http, station.latitude, station.longitude, base_url, need_prev=False)
    return to_response(scene, station, tz, coord)


async def history_times(http: httpx.AsyncClient):
    from datetime import timedelta

    from app.schemas.satellite import SatelliteHistoryResponse

    times = await himawari.available_times(http)
    end = times[-1]
    start = end - timedelta(hours=3)
    return SatelliteHistoryResponse(
        times=[t.isoformat() for t in times if start <= t <= end],
        start_at=start.isoformat(),
        end_at=end.isoformat(),
    )


_history_cache = AsyncTTLCache(256, 600)


async def cloud_at(
    http: httpx.AsyncClient, station: Station, when: datetime, coord: Coord, base_url: str
):
    from app.errors import ApiError

    manifest = await history_times(http)
    if when.isoformat() not in manifest.times:
        raise ApiError("SATELLITE_FRAME_UNAVAILABLE", "该观测时刻不在近三小时可用帧中", 404)
    bbox = station_bbox(station.latitude, station.longitude)
    key = f"{_bbox_key(bbox)}:{when.isoformat()}"
    path = tiles.tile_path("satellite-history", _bbox_key(bbox), f"{when:%Y%m%dT%H%M}_infrared")

    async def render():
        if not path.exists():
            mosaic = await himawari.fetch_mosaic(http, when, "infrared", bbox)
            rep = stretch_infrared(await _reproject(mosaic, bbox))
            tiles.write_tile(path, to_png(rep.rgb))
        return path

    await _history_cache.get_or_load(key, render)
    from app.schemas.layer import Bounds, LayerImage

    w, s, e, n = bbox
    return SatelliteCloudResponse(
        band="infrared",
        observed_at=when.isoformat(),
        image=LayerImage(
            url=f"{base_url}/tiles/{path.relative_to(tiles.tile_dir()).as_posix()}",
            bounds=Bounds(sw=_latlng(s, w, coord), ne=_latlng(n, e, coord)),
        ),
        station_marker=_latlng(station.latitude, station.longitude, coord),
        legend=Legend(
            title="红外云图",
            type="gradient",
            stops=None,
            labels=["低", "高"],
            colors=["#111827", "#ffffff"],
        ),
    )
