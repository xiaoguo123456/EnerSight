from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from app.config import settings
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
    # 15 m/s 在 100 m 已超额定：满发 × 24 h × (1 − 场站损耗)
    assert result.energy_kwh == pytest.approx(24000 * (1 - settings.wind_losses))
    assert sum(p.value for p in result.power_kw) == pytest.approx(result.energy_kwh)


def test_缺测不当零且负容量不计算():
    fc = weather.parse_forecast(forecast())
    day = fc.current_hour().normalize()
    today = slice(day, day + pd.Timedelta(hours=23))
    # 全天各层风速都缺：不可算，给 null 而不是 0
    for col in ("wind_speed_10m", "wind_speed_80m", "wind_speed_100m", "wind_speed_120m"):
        fc.hourly.loc[today, col] = np.nan
    out = prediction.compute(station(), fc)
    assert out.energy_kwh is None
    assert all(p.value is None for p in out.power_kw)
    st = station()
    st.capacity_kw = -1
    assert prediction.compute(st, weather.parse_forecast(forecast())).energy_kwh is None


def test_单小时缺测按前后插值而非归零():
    fc = weather.parse_forecast(forecast())
    for col in ("wind_speed_10m", "wind_speed_80m", "wind_speed_100m", "wind_speed_120m"):
        fc.hourly.loc[fc.current_hour(), col] = np.nan
    full = prediction.compute(station(), weather.parse_forecast(forecast()))
    out = prediction.compute(station(), fc)
    assert out.energy_kwh == pytest.approx(full.energy_kwh)


def test_只有10m风速时按幂律降级():
    fc = weather.parse_forecast(forecast())
    for col in ("wind_speed_80m", "wind_speed_100m", "wind_speed_120m"):
        fc.hourly[col] = np.nan
    out = prediction.compute(station(), fc)
    assert out.energy_kwh == pytest.approx(24000 * (1 - settings.wind_losses))


def test_光伏夜间零值与缺测不同():
    result = prediction.compute(station("solar"), weather.parse_forecast(forecast()))
    assert result.energy_kwh > 0
    assert result.power_kw[0].value == 0
    assert result.energy_kwh == pytest.approx(sum(p.value for p in result.power_kw), abs=0.03)


def batched(raw: dict):
    """按请求里的坐标数回相同条数的响应。

    多坐标请求的响应是个列表，条数必须与坐标数一致 —— 光伏与风电现在用不同步长，
    同一批里的坐标数不再恒等于 1。
    """

    def side_effect(request):
        n = len(request.url.params["latitude"].split(","))
        return httpx.Response(200, json=raw if n == 1 else [raw] * n)

    return side_effect


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
        respx.get(url__regex=r".*/static/meta\.json").mock(return_value=httpx.Response(404))
        route = respx.get(url__regex=r".*open-meteo.*/v1/forecast.*").mock(side_effect=batched(raw))
        async with httpx.AsyncClient() as c:
            await fleet.build(c, "gfs_global", fleet.day_key(), plants)
    out = fleet.load(tmp_path / f"gfs_global-{fleet.day_key()}.json")
    # status 说的是「该算的都算完了」；重复与非法记录是主动排除的，永远不会被覆盖，
    # 拿它们判断会让状态永远停在 partial。覆盖程度由 covered_count / total_count 表达。
    assert out["status"] == "ready" and out["failed_count"] == 0
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


async def test_有场站算不出时状态为partial(tmp_path, monkeypatch):
    """一个网格算失败：不能宣称算完，也不能把失败的场站记成零发电"""
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    from app.render import tiles

    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    raw = forecast()
    plants = [plant("a"), plant("c", lat=40.3)]
    real = fleet.calculate_cell

    def flaky(cell_plants, raw_, model, day, lat, lon):
        if cell_plants[0].id == "c":
            raise RuntimeError("气象缺口")
        return real(cell_plants, raw_, model, day, lat, lon)

    monkeypatch.setattr(fleet, "calculate_cell", flaky)
    with respx.mock:
        respx.get(url__regex=r".*open-meteo.*").mock(
            return_value=httpx.Response(200, json=[raw, raw])
        )
        async with httpx.AsyncClient() as c:
            await fleet.build(c, "gfs_global", fleet.day_key(), plants)
    out = fleet.load(tmp_path / f"gfs_global-{fleet.day_key()}.json")
    assert out["status"] == "partial"
    assert out["eligible_count"] == 2 and out["covered_count"] == 1 and out["failed_count"] == 1
    assert out["covered_capacity_kw"] == 1000 and out["total_capacity_kw"] == 2000
