"""合成 Himawari 圆盘：在指定经纬度处放一团有纹理的「云」。

纹理定义在经纬度空间（云团随中心整体平移），再逆投影到圆盘像素，
这样重投影回经纬度网格后就是干净的平移，光流能估出来。
"""

from datetime import UTC, datetime
from functools import lru_cache

import numpy as np
from pyproj import Transformer

from app.satellite.himawari import FullDisk
from app.satellite.reproject import GEOS, HALF_M, _tf

W = 2200  # 4d 全圆盘尺寸
_inv = Transformer.from_crs(GEOS, "EPSG:4326", always_xy=True)


def disk_px(lon: float, lat: float) -> tuple[float, float]:
    x, y = _tf.transform(lon, lat)
    return (x + HALF_M) / (2 * HALF_M) * W, (HALF_M - y) / (2 * HALF_M) * W


@lru_cache(maxsize=1)
def _disk_lonlat() -> tuple[np.ndarray, np.ndarray]:
    """每个圆盘像素的经纬度；圆盘外为 inf"""
    idx = (np.arange(W) + 0.5) / W
    x = idx * 2 * HALF_M - HALF_M
    y = HALF_M - idx * 2 * HALF_M
    xg, yg = np.meshgrid(x, y)
    lon, lat = _inv.transform(xg, yg)
    return lon, lat


def make_disk(
    when: datetime, blobs: list[tuple[float, float, float]] = (), *, night: bool = False
) -> FullDisk:
    """blobs: (lon, lat, radius_deg)。night=True 全黑。"""
    base = 0 if night else 45
    img = np.full((W, W), float(base))
    if blobs:
        lon, lat = _disk_lonlat()
        ok = np.isfinite(lon) & np.isfinite(lat)
        lon = np.where(ok, lon, 0)
        lat = np.where(ok, lat, 0)
        for clon, clat, r in blobs:
            dx, dy = lon - clon, lat - clat
            env = np.exp(-(dx**2 + dy**2) / (2 * r * r))
            # 多尺度纹理随云团中心平移
            tex = 0.7 + 0.15 * np.sin(dx * 40) * np.cos(dy * 50) + 0.15 * np.sin(dx * 13 + dy * 9)
            cloud = np.clip(env * tex, 0, 1) * 200 * ok
            img = np.maximum(img, base + cloud)
    rgb = np.repeat(img.astype(np.uint8)[..., None], 3, axis=2)
    return FullDisk(observed_at=when.astimezone(UTC), rgb=rgb)


def utc(h: int, m: int) -> datetime:
    return datetime.now(UTC).replace(hour=h, minute=m, second=0, microsecond=0)
