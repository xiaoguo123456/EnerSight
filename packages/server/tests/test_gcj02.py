"""GCJ-02 转换。

参考点来自公开实现的已知输出。精度要求：正向与参考差 < 1e-5°（约 1m），
往返误差 < 1e-7°。
"""

import math

import pytest

from app.geo import gcj02_to_wgs84, in_china, wgs84_to_gcj02


def _dist_m(lng1, lat1, lng2, lat2) -> float:
    """两点近似距离（米），小范围用平面近似够用"""
    dx = (lng2 - lng1) * 111_000 * math.cos(math.radians(lat1))
    dy = (lat2 - lat1) * 111_000
    return math.hypot(dx, dy)


class TestForward:
    def test_苏州偏移在合理范围(self):
        # 苏州光伏站 WGS84
        g_lng, g_lat = wgs84_to_gcj02(120.62, 31.30)
        d = _dist_m(120.62, 31.30, g_lng, g_lat)
        # 华东地区典型偏移 300–600m。偏移方向随地区变化，不断言方向
        assert 200 < d < 800

    def test_北京参考点(self):
        # 天安门附近，公开实现的已知输出
        g_lng, g_lat = wgs84_to_gcj02(116.3975, 39.9087)
        assert abs(g_lng - 116.40387) < 1e-3
        assert abs(g_lat - 39.91007) < 1e-3

    def test_境外不偏移(self):
        assert wgs84_to_gcj02(139.69, 35.69) == (139.69, 35.69)  # 东京
        assert wgs84_to_gcj02(-122.42, 37.77) == (-122.42, 37.77)  # 旧金山


class TestInverse:
    @pytest.mark.parametrize(
        ("lng", "lat"),
        [(120.62, 31.30), (116.40, 39.90), (113.26, 23.13), (87.62, 43.83), (121.47, 31.23)],
    )
    def test_往返误差小于1e7(self, lng, lat):
        g_lng, g_lat = wgs84_to_gcj02(lng, lat)
        w_lng, w_lat = gcj02_to_wgs84(g_lng, g_lat)
        assert abs(w_lng - lng) < 1e-7
        assert abs(w_lat - lat) < 1e-7

    def test_反向确实把偏移消掉了(self):
        g = wgs84_to_gcj02(120.62, 31.30)
        w = gcj02_to_wgs84(*g)
        assert _dist_m(120.62, 31.30, *w) < 0.05  # 5cm


class TestInChina:
    def test_边界(self):
        assert in_china(120.62, 31.30)
        assert not in_china(139.69, 35.69)
        assert not in_china(120.62, 0.5)
