"""卫星云图：拉全圆盘 → 站点周边重投影 → 落盘 → URL；两帧光流出云团外推。docs/05 §6.3、docs/07 §四

夜间真彩图全黑，NICT 公开源没有红外产品，此时接口返回 503 DATA_UNAVAILABLE，
预警页显示空态；地图云图层退回预报云量。红外接入 JAXA P-Tree 是 V2 的事。
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
import numpy as np

from app.errors import ApiError
from app.geo import wgs84_to_gcj02
from app.models import Station
from app.render import tiles
from app.satellite import himawari, motion
from app.satellite.reproject import Reprojected, is_daylit, reproject, to_png
from app.schemas.alert import CloudMotion
from app.schemas.common import Coord
from app.schemas.layer import Bounds, LatLng, LayerImage, Legend
from app.schemas.satellite import SatelliteCloudResponse

log = logging.getLogger(__name__)

HALF_SPAN_DEG = 2.5  # 站点周边 ±2.5°，约 500 km 见方，够看 2 小时外推
SIZE = 512
LEGEND = Legend(
    title="云量强度",
    type="gradient",
    stops=None,
    labels=["低", "高"],
    colors=["#1f2937", "#6b7280", "#ffffff"],
)


SceneStatus = Literal["ok", "night", "unavailable"]


@dataclass(frozen=True)
class CloudScene:
    observed_at: datetime  # UTC
    now: Reprojected
    prev: Reprojected | None
    url: str


def station_bbox(lat: float, lon: float) -> tuple[float, float, float, float]:
    # 按 0.5° 对齐，相邻站点共用一张图
    w = np.floor((lon - HALF_SPAN_DEG) * 2) / 2
    s = np.floor((lat - HALF_SPAN_DEG) * 2) / 2
    return float(w), float(s), float(w + 2 * HALF_SPAN_DEG), float(s + 2 * HALF_SPAN_DEG)


def _bbox_key(bbox: tuple[float, float, float, float]) -> str:
    w, s, _, _ = bbox
    return f"{s:+06.1f}_{w:+07.1f}"


async def _reproject(disk: himawari.FullDisk, bbox) -> Reprojected:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, reproject, disk.rgb, bbox, SIZE)


async def load_scene(
    http: httpx.AsyncClient, lat: float, lon: float, base_url: str, *, need_prev: bool
) -> CloudScene | None:
    """站点周边当前帧（及上一帧）。夜间返回 None。"""
    bbox = station_bbox(lat, lon)
    disk = await himawari.fetch_full_disk(http)
    now = await _reproject(disk, bbox)
    if not is_daylit(now.gray):
        return None

    time_key = disk.observed_at.strftime("%Y%m%dT%H%M")
    path = tiles.tile_path("satellite", _bbox_key(bbox), time_key)
    if not path.exists():
        tiles.write_tile(path, to_png(now.rgb))
    rel = path.relative_to(tiles.tile_dir()).as_posix()

    prev = None
    if need_prev:
        prev_disk = await himawari.fetch_previous(http, disk.observed_at)
        if prev_disk is not None:
            prev = await _reproject(prev_disk, bbox)
    return CloudScene(disk.observed_at, now, prev, f"{base_url}/tiles/{rel}")


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
        band="visible",
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
        scene.prev.gray, scene.now.gray, scene.now.bbox, station.latitude, station.longitude
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
    )


@dataclass(frozen=True)
class SceneResult:
    scene: CloudScene | None
    status: SceneStatus

    @property
    def known(self) -> bool:
        """是否拿到了确定的观测结论（有云图，或确定是夜间）"""
        return self.status != "unavailable"


async def load_scene_safely(
    http: httpx.AsyncClient, station: Station, base_url: str
) -> SceneResult:
    """卫星是增强项：夜间、上游故障都不让预报类预警跟着挂。"""
    try:
        scene = await load_scene(
            http, station.latitude, station.longitude, base_url, need_prev=True
        )
    except Exception:  # noqa: BLE001
        log.warning("satellite scene unavailable: station=%s", station.id, exc_info=True)
        return SceneResult(None, "unavailable")
    return SceneResult(scene, "ok" if scene else "night")


async def get_cloud(
    http: httpx.AsyncClient, station: Station, tz: str, coord: Coord, base_url: str
) -> SatelliteCloudResponse:
    scene = await load_scene(http, station.latitude, station.longitude, base_url, need_prev=False)
    if scene is None:
        raise ApiError("DATA_UNAVAILABLE", "夜间无可见光云图", 503)
    return to_response(scene, station, tz, coord)
