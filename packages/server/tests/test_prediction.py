from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pytest
import respx

from app.models import CatalogPlant, Station
from app.services import fleet_prediction as fleet
from app.services import prediction, weather
from tests.fixtures_forecast import make_forecast


def forecast():
    yesterday = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    return make_forecast(start_date=yesterday, wind_today=15)


def station(kind="wind"):
    return Station(
        type=kind,
        latitude=31.3,
        longitude=120.6,
        capacity_kw=1000,
        hub_height=100,
        tilt=None,
        azimuth=None,
    )


def plant(id, lat=31.3, capacity=1000, kind="wind", name=None):
    return CatalogPlant(
        id=id,
        source="gem",
        source_id=id,
        provenance={"phases": [{"capacity_kw": capacity, "capacity_rating": "ac"}]},
        name=name or id,
        type=kind,
        capacity_kw=capacity,
        latitude=lat,
        longitude=120.6,
        status="operating",
        province="江苏省",
    )


def test_日总量等于逐时功率积分():
    result = prediction.compute(station(), weather.parse_forecast(forecast()), "gfs_global")
    assert result.model == "gfs_global"
    assert len(result.power_kw) == 24
    assert result.energy_kwh == 24000
    assert sum(p.value for p in result.power_kw) == result.energy_kwh


def test_缺测不当零且负容量不计算():
    fc = weather.parse_forecast(forecast())
    fc.hourly.loc[fc.current_hour(), "wind_speed_10m"] = np.nan
    assert prediction.compute(station(), fc).energy_kwh is None
    st = station()
    st.capacity_kw = -1
    assert prediction.compute(st, weather.parse_forecast(forecast())).energy_kwh is None


def test_光伏夜间零值与缺测不同():
    result = prediction.compute(station("solar"), weather.parse_forecast(forecast()))
    assert result.energy_kwh > 0
    assert result.power_kw[0].value == 0
    assert result.energy_kwh == pytest.approx(sum(p.value for p in result.power_kw), abs=0.03)


async def test_汇总去重覆盖与贡献相加(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    raw = forecast()
    plants = [
        plant("a"),
        plant("b", capacity=2000, kind="solar"),
        plant("dup", name="a"),
        plant("invalid", lat=100, capacity=500),
    ]
    with respx.mock:
        route = respx.get(url__regex=r".*open-meteo.*").mock(
            return_value=httpx.Response(200, json=raw)
        )
        async with httpx.AsyncClient() as c:
            await fleet.build(c, "gfs_global", fleet.day_key(), plants)
    out = fleet.load(tmp_path / f"gfs_global-{fleet.day_key()}.json")
    assert out["status"] == "partial"
    assert out["duplicate_count"] == 1 and out["invalid_count"] == 1
    assert out["covered_count"] == 2 and out["total_count"] == 3
    assert out["covered_capacity_kw"] == 3000 and out["total_capacity_kw"] == 3500
    assert out["energy_kwh"] == pytest.approx(out["solar_kwh"] + out["wind_kwh"])
    assert out["energy_kwh"] == pytest.approx(sum(p["value"] for p in out["power_kw"]))
    assert route.call_count == 1
    assert route.calls[0].request.url.params["models"] == "gfs_global"


async def test_上游限流不伪造零值(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    with respx.mock:
        respx.get(url__regex=r".*open-meteo.*").mock(return_value=httpx.Response(429))
        async with httpx.AsyncClient() as c:
            await fleet.build(c, "icon_global", fleet.day_key(), [plant("a")])
    out = fleet.load(tmp_path / f"icon_global-{fleet.day_key()}.json")
    assert out["energy_kwh"] is None and out["covered_count"] == 0
    assert out["failed_count"] == 1 and out["power_kw"] == []


async def test_同模型任务去重且切换模型独立(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    # 替换排队运行函数访问的 SessionLocal，使测试不碰真实库。
    import asyncio

    gate = asyncio.Lock()
    await gate.acquire()
    monkeypatch.setattr(fleet, "_gate", gate)
    try:
        await fleet.ensure(None, "gfs_global")
        first = fleet._jobs.copy()
        await fleet.ensure(None, "gfs_global")
        await fleet.ensure(None, "ecmwf_ifs")
        assert len(fleet._jobs) == 2
        assert (
            fleet._jobs[f"gfs_global-{fleet.day_key()}"] is first[f"gfs_global-{fleet.day_key()}"]
        )
    finally:
        await fleet.shutdown()
