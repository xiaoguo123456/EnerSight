"""Web Mercator 马赛克 → 等经纬度网格。

小程序 ground-overlay 按经纬度 bounds 贴图，图内容要是等经纬度的；
光流的像素→公里换算也假定等经纬度。
"""

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates

from app.satellite.himawari import TILE, Mosaic, lonlat_to_tile


@dataclass(frozen=True)
class Reprojected:
    rgb: np.ndarray  # (H, W, 3)
    gray: np.ndarray  # (H, W) 亮度，云量强度代理，含义随波段
    bbox: tuple[float, float, float, float]  # w, s, e, n


def reproject(
    mosaic: Mosaic, bbox: tuple[float, float, float, float], size: int = 512
) -> Reprojected:
    """把 bbox（WGS84）重采样成 size×size 等经纬度图，双线性。"""
    w, s, e, n = bbox
    lons = np.linspace(w, e, size)
    lats = np.linspace(n, s, size)  # 图片行从北到南
    # 经度→列是线性的；纬度→行按 Mercator 逐行算
    n_tiles = 2**mosaic.zoom
    col = ((lons + 180.0) / 360.0 * n_tiles - mosaic.x0) * TILE
    row = np.array([lonlat_to_tile(0.0, float(lat), mosaic.zoom)[1] for lat in lats])
    row = (row - mosaic.y0) * TILE
    col_g, row_g = np.meshgrid(col, row)

    out = np.zeros((size, size, 3), dtype=np.uint8)
    for ch in range(3):
        out[..., ch] = map_coordinates(
            mosaic.rgb[..., ch].astype(float), [row_g, col_g], order=1, mode="nearest"
        ).astype(np.uint8)
    gray = (0.299 * out[..., 0] + 0.587 * out[..., 1] + 0.114 * out[..., 2]).astype(np.uint8)
    return Reprojected(rgb=out, gray=gray, bbox=bbox)


def to_png(rgb: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def to_jpeg(rgb: np.ndarray, quality: int = 85) -> bytes:
    """省域地图贴图：缩小传输体积，保留连续云纹理。"""
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
