"""静止轨道 → 经纬度网格重投影。

Himawari-8/9 投影参数（已用海岸线验证）：
  lon_0=140.7, h=35785863, sweep=y, 全圆盘固定网格半宽 5,500,000 m
"""

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image
from pyproj import Transformer
from scipy.ndimage import map_coordinates

GEOS = "+proj=geos +lon_0=140.7 +h=35785863 +a=6378137 +b=6356752.3 +units=m +sweep=y +no_defs"
HALF_M = 5_500_000.0
_tf = Transformer.from_crs("EPSG:4326", GEOS, always_xy=True)


@dataclass(frozen=True)
class Reprojected:
    rgb: np.ndarray  # (H, W, 3)
    gray: np.ndarray  # (H, W) 0–255 亮度，作为云量强度代理
    bbox: tuple[float, float, float, float]  # w, s, e, n


def reproject(
    disk_rgb: np.ndarray, bbox: tuple[float, float, float, float], size: int = 512
) -> Reprojected:
    """把 bbox（WGS84）内的区域重采样成 size×size 的等经纬度图。

    对目标网格每个像素求其在圆盘图上的位置，双线性采样。
    圆盘外（地球边缘之外）为 NaN → 黑。
    """
    w, s, e, n = bbox
    W = disk_rgb.shape[1]
    lons = np.linspace(w, e, size)
    lats = np.linspace(n, s, size)  # 图片行从北到南
    lon_g, lat_g = np.meshgrid(lons, lats)
    x, y = _tf.transform(lon_g, lat_g)
    col = (x + HALF_M) / (2 * HALF_M) * W
    row = (HALF_M - y) / (2 * HALF_M) * W
    valid = np.isfinite(col) & np.isfinite(row)
    col = np.where(valid, col, 0)
    row = np.where(valid, row, 0)

    out = np.zeros((size, size, 3), dtype=np.uint8)
    for ch in range(3):
        sampled = map_coordinates(
            disk_rgb[..., ch].astype(float), [row, col], order=1, mode="nearest"
        )
        out[..., ch] = np.where(valid, sampled, 0).astype(np.uint8)
    gray = (0.299 * out[..., 0] + 0.587 * out[..., 1] + 0.114 * out[..., 2]).astype(np.uint8)
    return Reprojected(rgb=out, gray=gray, bbox=bbox)


def to_png(rgb: np.ndarray, alpha: int | None = None) -> bytes:
    buf = io.BytesIO()
    if alpha is None:
        Image.fromarray(rgb, "RGB").save(buf, format="PNG", optimize=True)
    else:
        rgba = np.dstack([rgb, np.full(rgb.shape[:2], alpha, dtype=np.uint8)])
        Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def is_daylit(gray: np.ndarray, threshold: float = 18.0) -> bool:
    """真彩图夜间几乎全黑。平均亮度过低视为无可见光。"""
    return float(gray.mean()) > threshold
