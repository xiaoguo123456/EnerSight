"""合成 JMA 瓦片：在指定经纬度处放一团有纹理的「云」。

纹理定义在经纬度空间（云团随中心整体平移），按 Web Mercator 瓦片坐标逐像素求值，
这样重投影回等经纬度网格后就是干净的平移，光流能估出来。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from app.satellite import himawari
from app.satellite.himawari import TILE, tile_to_lonlat

Blob = tuple[float, float, float]  # lon, lat, radius_deg


@dataclass
class FakeSky:
    """按时刻记录云团位置，night=True 时可见光/真彩瓦片全黑（红外照常）。"""

    frames: dict[datetime, list[Blob]] = field(default_factory=dict)
    night_times: set[datetime] = field(default_factory=set)
    missing: set[datetime] = field(default_factory=set)  # 在列表里但瓦片还没出来
    calls: list[tuple[datetime, str]] = field(default_factory=list)

    def add(
        self, when: datetime, blobs: list[Blob] = (), *, night: bool = False, missing: bool = False
    ) -> "FakeSky":
        when = when.astimezone(UTC)
        self.frames[when] = list(blobs)
        if night:
            self.night_times.add(when)
        if missing:
            self.missing.add(when)
        return self

    def install(self, monkeypatch) -> "FakeSky":
        sky = self

        async def _times(_http):
            if not sky.frames:
                raise himawari.UpstreamUnavailable("no frames")
            return sorted(sky.frames)

        async def _tile(_http, when, band, x, y, _sem):
            sky.calls.append((when, band))
            if when not in sky.frames or when in sky.missing:
                raise himawari.UpstreamUnavailable("not ready")
            if band in ("visible", "truecolor") and when in sky.night_times:
                return np.zeros((TILE, TILE, 3), dtype=np.uint8)
            return render_tile(sky.frames[when], himawari.settings.himawari_zoom, x, y)

        monkeypatch.setattr(himawari, "available_times", _times)
        monkeypatch.setattr(himawari, "_fetch_tile", _tile)
        himawari.clear_cache()
        return sky


def render_tile(blobs: list[Blob], zoom: int, x: int, y: int) -> np.ndarray:
    px = (np.arange(TILE) + 0.5) / TILE
    lons = np.array([tile_to_lonlat(x + p, y, zoom)[0] for p in px])
    lats = np.array([tile_to_lonlat(x, y + p, zoom)[1] for p in px])
    lon, lat = np.meshgrid(lons, lats)
    img = np.full((TILE, TILE), 30.0)
    for clon, clat, r in blobs:
        dx, dy = lon - clon, lat - clat
        env = np.exp(-(dx**2 + dy**2) / (2 * r * r))
        tex = 0.7 + 0.15 * np.sin(dx * 40) * np.cos(dy * 50) + 0.15 * np.sin(dx * 13 + dy * 9)
        img = np.maximum(img, 30 + np.clip(env * tex, 0, 1) * 200)
    return np.repeat(img.astype(np.uint8)[..., None], 3, axis=2)


def utc(h: int, m: int, *, days_ago: int = 0) -> datetime:
    from datetime import timedelta

    return datetime.now(UTC).replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(
        days=days_ago
    )
