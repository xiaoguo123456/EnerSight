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
    """用晴空辐射乘一个折减系数模拟云天。与 Open-Meteo 同口径：前一小时均值。"""
    cs = solar.clearsky_hourly_mean(LAT, LON, TZ, times)
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
    def test_返回的是所问那一天(self):
        """pvlib 按 UTC 日期取日序，东八区的当地 00:00 还落在前一个 UTC 日 ——
        直接传当地零点会整体算成前一天的日出日落。"""
        for day in (date(2026, 9, 9), date(2026, 1, 1), date(2026, 6, 21)):
            rise, set_ = solar.daylight_window(LAT, LON, TZ, day)
            assert rise.date() == day and set_.date() == day
            assert rise < set_

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

    @pytest.mark.parametrize("day", ["2026-06-21", "2026-09-09", "2026-12-21"])
    def test_晴空小时均值恰为满分(self, day: str):
        """分子分母同口径（小时均值 + 区间中点太阳位置）：晴天就是 100，不是 98 或 99"""
        r = idx.pv_index(_pv_inputs(_times(day), cloudy=1.0, temp=25.0))
        assert r.score >= 99.5

    def test_逐时出力随分子一起返回且不超过交流容量(self):
        inp = _pv_inputs(_times("2026-06-21"), cloudy=1.0, temp=-5.0)  # 低温高辐射，直流侧超配
        r = idx.pv_index(inp)
        assert r.hourly_kw is not None and len(r.hourly_kw) == 24
        assert r.hourly_kw.max() <= inp.capacity_kw + 1e-6
        assert r.actual_kwh == pytest.approx(float(r.hourly_kw.sum()))

    def test_归因含辐射温度散热三项(self):
        r = idx.pv_index(_pv_inputs(_times("2026-06-21"), cloudy=0.7, temp=35.0))
        assert [a.factor.value for a in r.attribution] == ["radiation", "temperature", "wind"]

    def test_缺测输入直接拒绝而不是当零(self):
        inp = _pv_inputs(_times("2026-06-21"))
        inp.temp_air.iloc[12] = float("nan")
        with pytest.raises(ValueError):
            idx.pv_index(inp)

    def test_逐时链路缺测透传为NaN(self):
        inp = _pv_inputs(_times("2026-06-21"))
        inp.ghi.iloc[12] = float("nan")
        p = pv.hourly_power(inp)
        assert pd.isna(p.iloc[12]) and p.drop(p.index[12]).notna().all()
        assert p.iloc[0] == 0  # 夜间是 0，不是 NaN

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

    def test_缺测风速出力为NaN不当零(self):
        v = pd.Series([8.0, float("nan"), 8.0], index=pd.RangeIndex(3))
        p = wind.power_curve(v, 2000.0)
        assert pd.isna(p.iloc[1]) and p.iloc[0] > 0

    def test_场站损耗(self):
        from app.config import settings

        v = pd.Series([15.0], index=pd.RangeIndex(1))
        assert wind.plant_power(v, 2000.0).iloc[0] == pytest.approx(
            2000.0 * (1 - settings.wind_losses)
        )


class TestHubWind:
    def _levels(self, v10, v80, v100, v120):
        i = pd.RangeIndex(1)
        return {
            10.0: pd.Series([v10], index=i),
            80.0: pd.Series([v80], index=i),
            100.0: pd.Series([v100], index=i),
            120.0: pd.Series([v120], index=i),
        }

    def test_轮毂在两层之间按对数廓线插值(self):
        v, fb = wind.hub_wind_speed(self._levels(4.0, 6.0, 7.0, 7.5), 90.0)
        assert 6.0 < v.iloc[0] < 7.0 and not fb.iloc[0]
        # 对数廓线：90 m 比 80 m / 100 m 的算术中点略偏向 100 m
        assert v.iloc[0] > 6.5

    def test_轮毂恰在层上取该层(self):
        v, _ = wind.hub_wind_speed(self._levels(4.0, 6.0, 7.0, 7.5), 100.0)
        assert v.iloc[0] == pytest.approx(7.0)

    def test_高于最高层按最高两层斜率外推(self):
        v, fb = wind.hub_wind_speed(self._levels(4.0, 6.0, 7.0, 7.5), 140.0)
        assert 7.5 < v.iloc[0] < 8.5 and not fb.iloc[0]

    def test_有200m层时高于120m在层间插值而不外推(self):
        """ECMWF IFS 原生 200 m：夜间 100 m 以上风速几乎不再增加，沿 100–120 m 斜率外推会高估"""
        import math

        levels = self._levels(4.0, 6.0, 7.0, 7.5)
        levels[200.0] = pd.Series([7.6], index=pd.RangeIndex(1))
        v, fb = wind.hub_wind_speed(levels, 160.0)
        ratio = (math.log(160.0) - math.log(120.0)) / (math.log(200.0) - math.log(120.0))
        assert v.iloc[0] == pytest.approx(7.5 + 0.1 * ratio) and not fb.iloc[0]
        extrapolated, _ = wind.hub_wind_speed(self._levels(4.0, 6.0, 7.0, 7.5), 160.0)
        assert extrapolated.iloc[0] > v.iloc[0] + 0.5
        assert wind.LEVEL_COLUMNS[200.0] == "wind_speed_200m"

    def test_只有10m时按幂律降级并标记(self):
        nan = float("nan")
        v, fb = wind.hub_wind_speed(self._levels(5.0, nan, nan, nan), 100.0)
        assert v.iloc[0] == pytest.approx(wind.extrapolate_wind(pd.Series([5.0]), 100.0).iloc[0])
        assert fb.iloc[0]

    def test_轮毂低于最低有效层时向下外推而不是钳制(self):
        """10 m 缺测、轮毂 50 m：np.interp 会直接返回 80 m 的风速，系统性高估"""
        nan = float("nan")
        levels = self._levels(nan, 8.0, 10.0, 11.0)
        v, _ = wind.hub_wind_speed(levels, 50.0)
        assert 0.0 <= v.iloc[0] < 8.0
        # 仍在同一条对数廓线上：50 m 与 80 m 的差应与 80 m 到 100 m 的斜率一致
        import math

        slope = (10.0 - 8.0) / (math.log(100.0) - math.log(80.0))
        assert v.iloc[0] == pytest.approx(8.0 + slope * (math.log(50.0) - math.log(80.0)))

    def test_向下外推不为负(self):
        nan = float("nan")
        v, _ = wind.hub_wind_speed(self._levels(nan, 1.0, 9.0, 10.0), 12.0)
        assert v.iloc[0] == 0.0

    def test_全部缺测为NaN(self):
        nan = float("nan")
        v, _ = wind.hub_wind_speed(self._levels(nan, nan, nan, nan), 100.0)
        assert pd.isna(v.iloc[0])

    def test_昼夜廓线差异被保留(self):
        """同样的 10 m 风，夜间稳定层结 100 m 更强 —— 固定 α 外推抹平了这一点"""
        night, _ = wind.hub_wind_speed(self._levels(2.1, 4.3, 4.4, 4.5), 100.0)
        day, _ = wind.hub_wind_speed(self._levels(2.1, 2.7, 2.8, 2.9), 100.0)
        fixed = wind.extrapolate_wind(pd.Series([2.1]), 100.0).iloc[0]
        assert night.iloc[0] > fixed > day.iloc[0]


class TestWindIndex:
    @pytest.mark.parametrize(
        ("cf", "level"),
        [
            # 断点见 index._CF_POINTS，按 5 个风电基地校准。docs/07 §八
            (0.50, IndexLevel.EXCELLENT),
            (0.30, IndexLevel.GOOD),
            (0.15, IndexLevel.FAIR),
            (0.05, IndexLevel.POOR),
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
