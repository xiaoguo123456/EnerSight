"""自然日边界、混合能源时段与版本切换的回归验证。"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.metrics import pv, solar, wind
from app.services import energy, prediction, weather
from app.services.prediction_basis import version_for_day
from tests.test_prediction import station


@pytest.fixture(autouse=True)
def _cutover(monkeypatch):
    monkeypatch.setattr(settings, "model_v4_start_date", date(2026, 9, 10))


def forecast(monkeypatch, now="2026-09-10 12:30"):
    current = pd.Timestamp(now, tz="Asia/Shanghai")
    monkeypatch.setattr(weather.Forecast, "now", lambda self: current.to_pydatetime())
    index = pd.date_range(current.normalize() - pd.Timedelta(days=1), periods=72, freq="h")
    cs = solar.clearsky_hourly_mean(39.5, 74, "Asia/Shanghai", index)
    frame = pd.DataFrame(
        {
            "shortwave_radiation": cs.ghi,
            "direct_normal_irradiance": cs.dni,
            "diffuse_radiation": cs.dhi,
            "temperature_2m": 25.0,
            "wind_speed_10m": 2.0,
            "wind_speed_100m": 8.0,
        },
        index=index,
    )
    return weather.Forecast("Asia/Shanghai", frame)


def test_切换按目标日期而非请求时刻(monkeypatch):
    monkeypatch.setattr(settings, "model_v4_start_date", date(2026, 9, 10))
    assert version_for_day("2026-09-09") == "model-v3"
    assert version_for_day("2026-09-10") == "model-v4"
    fc = forecast(monkeypatch, "2026-09-09 12:30")
    today = energy.prepare(station("solar"), fc)
    tomorrow = energy.prepare(station("solar"), fc, day_offset=1)
    assert today.frame.index[0].hour == 0
    assert tomorrow.frame.index[0].hour == 1
    assert tomorrow.frame.index[-1] == pd.Timestamp("2026-09-11", tz=fc.tz)


def test_光伏与风电输出按相同区间起点对齐(monkeypatch):
    fc = forecast(monkeypatch)
    day = fc.current_hour().normalize()
    fc.hourly["shortwave_radiation"] = np.arange(len(fc.hourly))
    monkeypatch.setattr(pv, "hourly_power", lambda inp: inp.ghi)
    monkeypatch.setattr(wind, "plant_power", lambda speed, capacity, **_kw: speed * capacity)
    sol = station("solar")
    w = station("wind")
    ps = prediction.compute(sol, fc)
    pw = prediction.compute(w, fc)
    assert [p.time for p in ps.power_kw] == [p.time for p in pw.power_kw]
    assert pd.Timestamp(ps.power_kw[0].time) == day
    assert pd.Timestamp(ps.power_kw[-1].time) == day + pd.Timedelta(hours=23)
    expected = fc.hourly.loc[
        day + pd.Timedelta(hours=1) : day + pd.Timedelta(days=1), "shortwave_radiation"
    ]
    assert [p.value for p in ps.power_kw] == expected.tolist()
    assert ps.energy_kwh == expected.sum()
    assert pw.energy_kwh == 8 * w.capacity_kw * 24


@pytest.mark.parametrize("now", ["2026-06-21 23:30", "2026-09-10 23:30", "2026-09-10 00:00"])
def test_新旧口径日末与零时均读取真实区间(monkeypatch, now):
    fc = forecast(monkeypatch, now)
    st = station("solar")
    st.latitude, st.longitude = 39.5, 74.0
    actual = energy.compute(st, fc)
    expected = pv.hourly_power(energy.pv_inputs(st, fc.hourly, fc.tz)).loc[fc.current_interval()]
    assert actual.current_kw == pytest.approx(expected)
    if now.startswith("2026-06-21"):
        assert actual.hourly_kw.iloc[-1] > 0
        assert actual.current_kw == 0


def test_最低层以下外推必须标估算():
    _, fallback = wind.hub_wind_speed({80: pd.Series([8.0]), 100: pd.Series([10.0])}, 50)
    assert fallback.iloc[0]
