"""区间均值量与瞬时量的时间基准。docs/04 §二、docs/07 §2.1

Open-Meteo 的辐射是前一小时均值标在区间末（13:00 的值覆盖 12:00–13:00），
气温、风速、云量、weather_code 则是标注时刻的瞬时值。两者的「当前」不是同一格，
用同一个整点去取会让日出后一小时的功率与辐射低估几倍。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.models import Station
from app.services import alerts, energy, weather
from app.services.home import build_current_weather
from tests.fixtures_forecast import TZ, make_forecast

LAT, LON = 31.30, 120.62


def _forecast(**kw) -> weather.Forecast:
    yesterday = (datetime.now(ZoneInfo(TZ)) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    return weather.parse_forecast(make_forecast(start_date=yesterday, **kw))


def _at(monkeypatch, hhmm: str) -> None:
    """把「现在」固定到今天的某个时刻。"""
    h, m = (int(x) for x in hhmm.split(":"))
    fixed = datetime.now(ZoneInfo(TZ)).replace(hour=h, minute=m, second=0, microsecond=0)
    monkeypatch.setattr(weather.Forecast, "now", lambda self: fixed)


def _station(kind: str = "solar", **kw) -> Station:
    return Station(
        id="s1",
        owner_id="u",
        name="x",
        type=kind,
        latitude=LAT,
        longitude=LON,
        capacity_kw=kw.pop("capacity_kw", 500.0),
        tilt=None,
        azimuth=None,
        hub_height=100.0 if kind == "wind" else None,
        **kw,
    )


class TestLabels:
    def test_半点时区间标签比整点晚一格(self, monkeypatch):
        _at(monkeypatch, "06:30")
        fc = _forecast()
        assert fc.current_hour().strftime("%H:%M") == "06:00"
        assert fc.current_interval().strftime("%H:%M") == "07:00"

    def test_整点时两个标签相同(self, monkeypatch):
        _at(monkeypatch, "07:00")
        fc = _forecast()
        assert fc.current_hour() == fc.current_interval()

    def test_光伏取区间末风电取整点(self, monkeypatch):
        _at(monkeypatch, "06:30")
        fc = _forecast()
        hour = fc.current_hour()
        assert energy.current_label(fc, "solar") == hour + pd.Timedelta(hours=1)
        assert energy.current_label(fc, "wind") == hour


class TestCurrentPower:
    def test_光伏当前功率取包含当前时刻的区间(self, monkeypatch):
        _at(monkeypatch, "06:30")
        fc = _forecast()
        snap = energy.compute(_station(), fc)
        assert snap.current_kw == pytest.approx(float(snap.hourly_kw.loc[fc.current_interval()]))
        # 06:00 那一格是 05–06 的均值，日出前后差着几倍，不能拿它当「当前」
        assert float(snap.hourly_kw.loc[fc.current_hour()]) < snap.current_kw

    def test_风电当前功率取当前整点(self, monkeypatch):
        _at(monkeypatch, "06:30")
        fc = _forecast(wind_today=8.0)
        snap = energy.compute(_station("wind", capacity_kw=2000.0), fc)
        assert snap.current_kw == pytest.approx(float(snap.hourly_kw.loc[fc.current_hour()]))

    def test_日末不因区间标签越界变成缺测(self, monkeypatch):
        """23:30 的区间末标签是次日 00:00，落在今日帧外 —— 该显示 0 而不是「数据获取中」"""
        _at(monkeypatch, "23:30")
        fc = _forecast()
        snap = energy.compute(_station(), fc)
        assert fc.current_interval() not in snap.hourly_kw.index
        assert snap.current_kw == 0.0


class TestCurrentWeather:
    def test_辐射按区间末其余按整点(self, monkeypatch):
        _at(monkeypatch, "06:30")
        fc = _forecast()
        hour = fc.current_hour()
        cw = build_current_weather(fc)
        assert cw.radiation.value == pytest.approx(
            float(fc.hourly.loc[hour + pd.Timedelta(hours=1), "shortwave_radiation"])
        )
        assert cw.temperature.value == pytest.approx(float(fc.hourly.loc[hour, "temperature_2m"]))
        assert cw.observed_at == hour.isoformat()

    def test_辐射环比也对齐同一格(self, monkeypatch):
        _at(monkeypatch, "10:30")
        fc = _forecast(peak_today=800.0, peak_yesterday=400.0)
        label = fc.current_hour() + pd.Timedelta(hours=1)
        now_v = float(fc.hourly.loc[label, "shortwave_radiation"])
        y_v = float(fc.hourly.loc[label - pd.Timedelta(days=1), "shortwave_radiation"])
        cw = build_current_weather(fc)
        assert cw.radiation.delta_percent == pytest.approx(round((now_v - y_v) / abs(y_v) * 100, 1))


class TestCloudDropBaseline:
    def test_当前基准是包含当前时刻的区间(self, monkeypatch):
        """上一格很亮、当前格起转阴：拿上一格当基准会报出一个并不存在的「大幅下降」"""
        from app.metrics import solar

        _at(monkeypatch, "10:30")
        fc = _forecast()
        day = fc.current_hour().normalize()
        prev, cur = day + pd.Timedelta(hours=10), day + pd.Timedelta(hours=11)
        clear = solar.clearsky_hourly_mean(LAT, LON, TZ, pd.DatetimeIndex([prev]))["ghi"].iloc[0]
        fc.hourly.loc[prev, "shortwave_radiation"] = float(clear) * 0.95
        fc.hourly.loc[cur:, "shortwave_radiation"] = 5.0
        # 当前已经是阴天（kt 低于 MIN_KT_NOW），没有「将要下降」这回事
        assert alerts.detect_cloud_drop(fc, LAT, LON) is None
