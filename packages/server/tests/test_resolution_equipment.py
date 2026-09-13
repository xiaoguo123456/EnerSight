"""15 分钟曲线、空气密度修正、机型档与光伏安装方式。docs/07 §2.1、§2.2、§2.7"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import respx
from httpx import AsyncClient, Response

from app.config import settings
from app.metrics import wind
from app.models import Station
from app.services import energy, prediction, weather
from tests.fixtures_forecast import TZ, make_forecast

WIND = {
    "name": "高原风电",
    "type": "wind",
    "latitude": 36.28,
    "longitude": 100.62,
    "capacity": 2000,
}


def _yesterday_midnight() -> datetime:
    now = datetime.now(ZoneInfo(TZ))
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def _forecast(**kw) -> dict:
    return make_forecast(start_date=_yesterday_midnight(), **kw)


@pytest.fixture(autouse=True)
def _fresh_cache():
    weather.clear_cache()
    yield
    weather.clear_cache()


def _station(kind="wind", **extra) -> Station:
    return Station(
        id="abc123def456",
        owner_id="u",
        type=kind,
        latitude=36.28,
        longitude=100.62,
        capacity_kw=1000,
        hub_height=100,
        tilt=None,
        azimuth=None,
        **extra,
    )


# ────────────────────────────── 15 分钟 ──────────────────────────────


def test_七天均为15分钟96点():
    fc = weather.parse_forecast(_forecast(wind_today=8))
    assert fc.quarter is not None and len(fc.quarter) == 9 * 96
    out = prediction.compute_days(_station(), fc, 7)
    assert [d.resolution_minutes for d in out.days] == [15] * 7
    assert [len(d.power_kw) for d in out.days] == [96] * 7
    # 恒定风速：15 分钟积分与逐小时积分相同
    # 风速恒定、气温日变化让密度略变：15 分钟积分与逐小时积分只差插值误差
    hourly = prediction.compute(
        _station(), weather.parse_forecast(_forecast(wind_today=8, minutely=False)), day_offset=1
    ).energy_kwh
    assert out.days[1].energy_kwh == pytest.approx(hourly, rel=1e-3)
    again = prediction.compute_days(_station(), fc, 2).days[1].index_score
    assert out.days[1].index_score == pytest.approx(again)


def test_光伏15分钟曲线与逐小时电量接近_标签从00点15起():
    fc = weather.parse_forecast(_forecast())
    fine = prediction.compute(_station("solar"), fc, day_offset=1, step_minutes=15)
    coarse = prediction.compute(
        _station("solar"), weather.parse_forecast(_forecast(minutely=False)), day_offset=1
    )
    assert fine.resolution_minutes == 15 and len(fine.power_kw) == 96
    assert fine.energy_kwh == pytest.approx(coarse.energy_kwh, rel=0.08)
    # 响应统一为区间起点标注：首点 00:00，末点 23:45
    assert fine.power_kw[0].time.endswith("T00:00:00+08:00")
    assert fine.power_kw[-1].time.endswith("T23:45:00+08:00")
    assert fine.energy_kwh == pytest.approx(sum(p.value for p in fine.power_kw) / 4, abs=0.05)


def test_历史小时资料保持真实分辨率():
    fc = weather.parse_forecast(_forecast(minutely=False))
    out = prediction.compute_days(_station(), fc, 7)
    assert all(d.resolution_minutes == 60 and len(d.power_kw) == 24 for d in out.days)


# ────────────────────────────── 空气密度 ──────────────────────────────


def test_高海拔密度修正使额定以下出力下降两到三成():
    # 夹具 100 m 风速 = 10 m 风速 × 10^0.18：wind_today=5 对应轮毂 7.6 m/s，在爬坡段
    low = prediction.compute(
        _station(), weather.parse_forecast(_forecast(wind_today=5, elevation=5))
    )
    high = prediction.compute(
        _station(), weather.parse_forecast(_forecast(wind_today=5, elevation=3000))
    )
    ratio = high.energy_kwh / low.energy_kwh
    assert 0.6 < ratio < 0.8
    assert any("空气密度" in a and "海拔 3000 m" in a for a in high.assumptions)
    # 额定以上不受影响：15 m/s 满发
    full_low = prediction.compute(_station(), weather.parse_forecast(_forecast(wind_today=15)))
    full_high = prediction.compute(
        _station(), weather.parse_forecast(_forecast(wind_today=15, elevation=3000))
    )
    assert full_high.energy_kwh == pytest.approx(full_low.energy_kwh)


def test_缺气压按海拔ISA_缺海拔按标称():
    import pandas as pd

    idx = pd.date_range("2026-09-14", periods=3, freq="h", tz=TZ)
    temp = pd.Series([15.0, 15.0, 15.0], index=idx)
    rho_isa = wind.air_density(None, temp, 3000.0)
    assert 0.82 < rho_isa.iloc[0] < 0.90  # ISA 3000 m、15℃ ≈ 0.85
    rho_ref = wind.air_density(None, temp, None)
    assert rho_ref.iloc[0] == settings.wind_air_density_ref
    rho_p = wind.air_density(pd.Series([1013.25] * 3, index=idx), temp, 3000.0)
    assert rho_p.iloc[0] == pytest.approx(101325 / (287.05 * 288.15), rel=1e-4)


# ────────────────────────────── 机型 ──────────────────────────────


def test_机型档与自定义曲线():
    # wind_today=6.5 → 轮毂约 9.8 m/s：低风速档（额定 9.5）满发，通用档（额定 12）仍在爬坡段
    fc = weather.parse_forecast(_forecast(wind_today=6.5, elevation=5))
    generic = prediction.compute(_station(), fc).energy_kwh
    low = prediction.compute(_station(turbine_class="low_wind"), fc).energy_kwh
    assert low == pytest.approx(24 * 1000 * (1 - settings.wind_losses), rel=0.02)
    assert low > generic * 1.3
    curve = [{"v": 3, "p": 0}, {"v": 8, "p": 50}, {"v": 12, "p": 100}, {"v": 20, "p": 100}]
    custom = prediction.compute(
        _station(turbine_class="custom", power_curve=curve),
        weather.parse_forecast(_forecast(wind_today=8 / 10**0.18, elevation=5)),
    )
    # 轮毂 8 m/s、密度接近标称：落在 50% 点附近
    assert custom.energy_kwh == pytest.approx(24 * 500 * (1 - settings.wind_losses), rel=0.05)
    assert any("自定义功率曲线" in a for a in custom.assumptions)
    # 末点之后视作切出
    beyond = prediction.compute(
        _station(turbine_class="custom", power_curve=curve),
        weather.parse_forecast(_forecast(wind_today=22, elevation=5)),
    )
    assert beyond.energy_kwh == 0


# ────────────────────────────── 光伏安装方式 ──────────────────────────────


def test_单轴跟踪与双面高于固定单面():
    fc = weather.parse_forecast(_forecast(cloud_today=0))
    fixed = prediction.compute(_station("solar"), fc).energy_kwh
    tracking = prediction.compute(_station("solar", mounting="single_axis"), fc).energy_kwh
    bifacial = prediction.compute(_station("solar", bifacial=True), fc).energy_kwh
    assert tracking > fixed * 1.05
    assert fixed * 1.03 < bifacial < fixed * 1.25
    both = prediction.compute(_station("solar", mounting="single_axis", bifacial=True), fc)
    assert both.energy_kwh > tracking
    assert any("单轴跟踪" in a for a in both.assumptions)
    assert any("双面" in a for a in both.assumptions)
    # 指数用同一套安装方式算理想值，不会因跟踪而超过 100
    snap = energy.compute(_station("solar", mounting="single_axis"), fc)
    assert snap.index is not None and snap.index.score <= 100


# ────────────────────────────── 接口 ──────────────────────────────


@pytest.fixture
def open_meteo():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta\.json").mock(return_value=Response(404))
        yield mock.get(url__regex=r".*/v1/forecast.*").mock(
            return_value=Response(200, json=_forecast(wind_today=9, elevation=3000))
        )


class TestEquipmentApi:
    async def test_自定义机型必须带曲线且风速递增(self, client: AsyncClient):
        r = await client.post("/v1/stations", json={**WIND, "turbine_class": "custom"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM"
        bad = [{"v": 3, "p": 0}, {"v": 3, "p": 50}, {"v": 12, "p": 100}]
        body = {**WIND, "turbine_class": "custom", "power_curve": bad}
        assert (await client.post("/v1/stations", json=body)).status_code == 400
        body["power_curve"] = bad[:2]
        assert (await client.post("/v1/stations", json=body)).status_code == 400

    async def test_机型与安装方式回显并进入预测(self, client: AsyncClient, open_meteo):
        curve = [{"v": 3, "p": 0}, {"v": 9, "p": 60}, {"v": 12, "p": 100}, {"v": 25, "p": 100}]
        r = await client.post(
            "/v1/stations", json={**WIND, "turbine_class": "custom", "power_curve": curve}
        )
        assert r.status_code == 201, r.text
        d = r.json()["data"]
        assert d["turbine_class"] == "custom" and d["power_curve"] == curve
        assert d["mounting"] is None and d["bifacial"] is None
        home = (await client.get("/v1/home", params={"station_id": d["id"]})).json()["data"]
        assert any("自定义功率曲线" in a for a in home["prediction"]["assumptions"])
        assert any("海拔 3000 m" in a for a in home["prediction"]["assumptions"])
        patch = {"turbine_class": "low_wind", "power_curve": None}
        r = await client.patch(f"/v1/stations/{d['id']}", json=patch)
        assert r.json()["data"]["turbine_class"] == "low_wind"
        pv_body = {**WIND, "name": "跟踪光伏", "type": "solar"}
        pv_body.update(mounting="single_axis", bifacial=True)
        solar = await client.post("/v1/stations", json=pv_body)
        assert solar.status_code == 201
        s = solar.json()["data"]
        assert s["mounting"] == "single_axis" and s["bifacial"] is True
        out = (
            await client.get("/v1/predictions/station", params={"station_id": s["id"], "days": 5})
        ).json()["data"]
        assert [x["resolution_minutes"] for x in out["days"]] == [15] * 5
        assert len(out["days"][0]["power_kw"]) == 96 and len(out["days"][4]["power_kw"]) == 96
