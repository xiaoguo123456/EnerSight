"""指标计算测试。

重点验证结构性质，不验证具体数值 —— 数值需按 docs/07 §八 在真实
历史数据上校准，设计稿里的数字是视觉示意值不能用来对账。
"""

from datetime import date

import pandas as pd
import pytest

from app.metrics import index as idx
from app.metrics import pv, solar, wind
from app.schemas.common import IndexLevel

LAT, LON, TZ = 31.30, 120.62, "Asia/Shanghai"


def _times(day: str) -> pd.DatetimeIndex:
    return pd.date_range(f"{day} 00:00", periods=24, freq="h", tz=TZ)


def _pv_inputs(times: pd.DatetimeIndex, *, cloudy: float = 1.0, temp: float = 25.0):
    """用晴空辐射乘一个折减系数模拟云天。"""
    cs = solar.clearsky(LAT, LON, TZ, times)
    return idx.PvInputs(
        latitude=LAT,
        longitude=LON,
        tz=TZ,
        capacity_kw=500.0,
        tilt=pv.default_tilt(LAT),
        azimuth=180.0,
        times=times,
        ghi=cs["ghi"] * cloudy,
        dni=cs["dni"] * cloudy,
        dhi=cs["dhi"] * cloudy,
        temp_air=pd.Series(temp, index=times),
        wind_speed=pd.Series(1.0, index=times),
    )


class TestDaylight:
    def test_夏至日照长于冬至(self):
        _, summer_set = solar.daylight_window(LAT, LON, TZ, date(2026, 6, 21))
        summer_rise, _ = solar.daylight_window(LAT, LON, TZ, date(2026, 6, 21))
        winter_rise, winter_set = solar.daylight_window(LAT, LON, TZ, date(2026, 12, 21))
        assert (summer_set - summer_rise) > (winter_set - winter_rise)

    def test_高纬度冬季日照远短于低纬度(self):
        """固定 06:00-18:00 窗口会在这种场景失真，所以要按经纬度算。"""
        mohe_rise, mohe_set = solar.daylight_window(52.97, 122.53, TZ, date(2026, 12, 21))
        sz_rise, sz_set = solar.daylight_window(22.54, 114.06, TZ, date(2026, 12, 21))
        assert (mohe_set - mohe_rise).total_seconds() / 3600 < 8
        assert (sz_set - sz_rise) > (mohe_set - mohe_rise)


class TestPvIndex:
    def test_晴空条件下接近满分(self):
        r = idx.pv_index(_pv_inputs(_times("2026-06-21"), cloudy=1.0, temp=25.0))
        assert r.score > 95

    def test_云量增加使分数下降(self):
        clear = idx.pv_index(_pv_inputs(_times("2026-06-21"), cloudy=1.0))
        cloudy = idx.pv_index(_pv_inputs(_times("2026-06-21"), cloudy=0.5))
        assert cloudy.score < clear.score

    def test_高温使分数下降(self):
        cool = idx.pv_index(_pv_inputs(_times("2026-06-21"), temp=25.0))
        hot = idx.pv_index(_pv_inputs(_times("2026-06-21"), temp=40.0))
        assert hot.score < cool.score

    def test_无辐射时为零分不可被其他因子补偿(self):
        """加权求和方案的补偿性缺陷——这里必须是 0。docs/07 §1.2"""
        r = idx.pv_index(_pv_inputs(_times("2026-06-21"), cloudy=0.0, temp=25.0))
        assert r.score == 0.0
        assert r.level is IndexLevel.POOR

    def test_分数恒在0到100之间(self):
        for c in (0.0, 0.3, 0.7, 1.0):
            r = idx.pv_index(_pv_inputs(_times("2026-03-21"), cloudy=c))
            assert 0.0 <= r.score <= 100.0

    def test_归因中辐射项为负(self):
        r = idx.pv_index(_pv_inputs(_times("2026-06-21"), cloudy=0.6))
        rad = next(a for a in r.attribution if a.factor.value == "radiation")
        assert rad.delta < 0


class TestWindPowerCurve:
    def test_切入风速以下不发电(self):
        v = pd.Series([0.0, 1.0, 2.9], index=pd.RangeIndex(3))
        assert wind.power_curve(v, 2000.0).eq(0).all()

    def test_切出风速以上不发电(self):
        v = pd.Series([25.1, 30.0], index=pd.RangeIndex(2))
        assert wind.power_curve(v, 2000.0).eq(0).all()

    def test_额定区间满发(self):
        v = pd.Series([12.0, 18.0, 25.0], index=pd.RangeIndex(3))
        assert wind.power_curve(v, 2000.0).eq(2000.0).all()

    def test_爬坡段单调递增(self):
        v = pd.Series([3.0, 6.0, 9.0, 12.0], index=pd.RangeIndex(4))
        p = wind.power_curve(v, 2000.0)
        assert p.is_monotonic_increasing

    def test_风速外推随高度增大(self):
        v10 = pd.Series([5.0], index=pd.RangeIndex(1))
        assert wind.extrapolate_wind(v10, 85.0).iloc[0] > v10.iloc[0]


class TestWindIndex:
    @pytest.mark.parametrize(
        ("cf", "level"),
        [
            (0.50, IndexLevel.EXCELLENT),
            (0.35, IndexLevel.GOOD),
            (0.22, IndexLevel.FAIR),
            (0.10, IndexLevel.POOR),
        ],
    )
    def test_容量因子分档(self, cf: float, level: IndexLevel):
        cap = 2000.0
        r = idx.wind_index(cf * cap * 24.0, cap)
        assert r.level is level

    def test_零出力为零分(self):
        assert idx.wind_index(0.0, 2000.0).score == 0.0

    def test_分数随容量因子单调不减(self):
        cap = 2000.0
        scores = [
            idx.wind_index(cf * cap * 24.0, cap).score for cf in (0.05, 0.15, 0.25, 0.35, 0.5, 0.8)
        ]
        assert scores == sorted(scores)
