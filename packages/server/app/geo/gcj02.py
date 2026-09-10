"""WGS84 ↔ GCJ-02。

GCJ-02 是中国特有的非线性加密偏移，不是投影，pyproj 不含。
Open-Meteo / Himawari 用 WGS84，腾讯地图用 GCJ-02，境内偏移几十到几百米。
存储与计算一律 WGS84，仅在响应序列化时转换。docs/05 §6.6、docs/06 §2.2

正向变换是公开的标准实现；反向没有解析解，用迭代逼近。
"""

import math

_A = 6378245.0
_EE = 0.00669342162296594323


def in_china(lng: float, lat: float) -> bool:
    """粗略的境内判断。境外不做偏移 —— 腾讯地图在境外也用 WGS84。"""
    return 72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    """返回 (lng, lat)。参数顺序是 (lng, lat)，与常见 GIS 库一致，注意别传反。"""
    if not in_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float, *, iterations: int = 8) -> tuple[float, float]:
    """反向变换。无解析解，迭代逼近，8 轮误差 < 1e-7°（约 1cm）。"""
    if not in_china(lng, lat):
        return lng, lat
    w_lng, w_lat = lng, lat
    for _ in range(iterations):
        g_lng, g_lat = wgs84_to_gcj02(w_lng, w_lat)
        w_lng += lng - g_lng
        w_lat += lat - g_lat
    return w_lng, w_lat


# 显示栅格逐像素反变换；与上面的标量变换使用同一套公式。
import numpy as np  # noqa: E402


def _array_lat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * np.sqrt(abs(x))
    ret += (20.0 * np.sin(6.0 * x * np.pi) + 20.0 * np.sin(2.0 * x * np.pi)) * 2.0 / 3.0
    ret += (20.0 * np.sin(y * np.pi) + 40.0 * np.sin(y / 3.0 * np.pi)) * 2.0 / 3.0
    ret += (160.0 * np.sin(y / 12.0 * np.pi) + 320 * np.sin(y * np.pi / 30.0)) * 2.0 / 3.0
    return ret


def _array_lng(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * np.sqrt(abs(x))
    ret += (20.0 * np.sin(6.0 * x * np.pi) + 20.0 * np.sin(2.0 * x * np.pi)) * 2.0 / 3.0
    ret += (20.0 * np.sin(x * np.pi) + 40.0 * np.sin(x / 3.0 * np.pi)) * 2.0 / 3.0
    ret += (150.0 * np.sin(x / 12.0 * np.pi) + 300.0 * np.sin(x / 30.0 * np.pi)) * 2.0 / 3.0
    return ret


def gcj02_to_wgs84_array(lng, lat):
    """批量反变换，仅供显示栅格；境外保持原坐标。"""
    lng, lat = np.asarray(lng, dtype=float), np.asarray(lat, dtype=float)
    wl, wt = lng.copy(), lat.copy()
    inside = (lng >= 72.004) & (lng <= 137.8347) & (lat >= 0.8293) & (lat <= 55.8271)
    for _ in range(8):
        rad = wt / 180 * np.pi
        magic = 1 - _EE * np.sin(rad) ** 2
        sqrtmagic = np.sqrt(magic)
        dy = _array_lat(wl - 105, wt - 35) * 180 / ((_A * (1 - _EE)) / (magic * sqrtmagic) * np.pi)
        dx = _array_lng(wl - 105, wt - 35) * 180 / (_A / sqrtmagic * np.cos(rad) * np.pi)
        wl = np.where(inside, lng - dx, lng)
        wt = np.where(inside, lat - dy, lat)
    return wl, wt
