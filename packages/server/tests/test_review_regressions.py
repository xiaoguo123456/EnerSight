"""审查发现的业务边界回归：参数、运行阈值、覆盖与可复现性。"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from app.cache import AsyncTTLCache
from app.config import settings
from app.metrics.wind import Turbine, plant_power
from app.models import DailyGeneration
from app.render import tiles
from app.services import accumulate, fleet_history, prediction, prediction_archive, weather
from app.services import fleet_prediction as fleet
from app.services.curtailment import Window
from app.services.prediction_basis import calculation_version
from tests.test_accumulate import SUZHOU, _session
from tests.test_prediction import batched, forecast, plant, station


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    weather.clear_cache()
    yield
    weather.clear_cache()


@pytest.mark.parametrize("rho", [0.9, 1.225, 1.5])
@pytest.mark.parametrize(
    "turbine,cutout",
    [
        (Turbine(), 25),
        (Turbine("low_wind"), 22),
        (Turbine("custom", ((3, 0), (12, 1), (20, 1))), 20),
    ],
)
def test_密度不能改变真实风速切出边界(rho, turbine, cutout):
    speed = pd.Series([2.0, cutout - 0.1, cutout + 0.1, np.nan])
    power = plant_power(speed, 1000, rho=pd.Series([rho] * 4), turbine=turbine)
    assert power.iloc[0] == 0
    assert power.iloc[1] > 0
    assert power.iloc[2] == 0
    assert np.isnan(power.iloc[3])


def test_周五跨夜规则作用于周六凌晨():
    rule = Window(22, 2, 0, (5,))
    assert rule.covers(pd.Timestamp("2026-09-11 23:00", tz="Asia/Shanghai"))
    assert rule.covers(pd.Timestamp("2026-09-12 01:00", tz="Asia/Shanghai"))
    assert not rule.covers(pd.Timestamp("2026-09-11 01:00", tz="Asia/Shanghai"))
    assert not rule.covers(pd.Timestamp("2026-09-12 02:00", tz="Asia/Shanghai"))


async def test_编辑名称后设备回显和预测不变(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*open-meteo.*").respond(200, json=forecast())
        created = (
            await client.post(
                "/v1/stations", json={**SUZHOU, "tilt": 20, "azimuth": 160, "hub_height": 120}
            )
        ).json()["data"]
        sid = created["id"]
        before = (await client.get("/v1/predictions/station", params={"station_id": sid})).json()[
            "data"
        ]
        updated = (await client.patch(f"/v1/stations/{sid}", json={"name": "改名"})).json()["data"]
        detail = (await client.get(f"/v1/stations/{sid}/detail")).json()["data"]["station"]
        after = (await client.get("/v1/predictions/station", params={"station_id": sid})).json()[
            "data"
        ]
    for field, value in [("tilt", 20), ("azimuth", 160), ("hub_height", 120)]:
        assert created[field] == updated[field] == detail[field] == value
    assert before["days"] == after["days"]


async def test_缓存锁并发复用且完成后清理():
    cache = AsyncTTLCache(2, 60)
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return 42

    assert await asyncio.gather(*(cache.get_or_load("同键", load) for _ in range(20))) == [42] * 20
    assert calls == 1
    for i in range(20):
        await cache.get_or_load(str(i), load)
    assert not cache._locks and not cache._users


async def test_缓存等待取消和异常不泄漏锁():
    cache = AsyncTTLCache(2, 60)
    event = asyncio.Event()

    async def load():
        await event.wait()
        raise ValueError("上游失败")

    first = asyncio.create_task(cache.get_or_load("同键", load))
    second = asyncio.create_task(cache.get_or_load("同键", load))
    await asyncio.sleep(0)
    second.cancel()
    event.set()
    await asyncio.gather(first, second, return_exceptions=True)
    assert not cache._locks and not cache._users


async def test_同旧网格不同站点分别拉取():
    params = []

    def response(request):
        params.append(dict(request.url.params))
        return httpx.Response(200, json=forecast())

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta.json").respond(404)
        mock.get(url__regex=r".*/v1/forecast.*").mock(side_effect=response)
        async with httpx.AsyncClient() as http:
            await weather.get_forecast(http, 31.26, 120.56)
            await weather.get_forecast(http, 31.34, 120.64)
    assert len(params) == 2
    assert params[0]["latitude"] == "31.26" and params[1]["latitude"] == "31.34"


async def test_当地跨午夜即使批次没变也刷新(monkeypatch):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta.json").respond(404)
        route = mock.get(url__regex=r".*/v1/forecast.*").respond(200, json=forecast())
        async with httpx.AsyncClient() as http:
            base = await weather.get_forecast(http, 31.3, 120.6)
            monkeypatch.setattr(
                weather.Forecast,
                "now",
                lambda self: base.fetched_at.astimezone(ZoneInfo(base.tz)) + timedelta(days=1),
            )
            await weather.get_forecast(http, 31.3, 120.6)
    assert route.call_count == 2


async def test_昨日记录不冒充今日且旧功率隐藏(client):
    sid = (await client.post("/v1/stations", json=SUZHOU)).json()["data"]["id"]
    now = datetime.now(UTC)
    today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    db, gen = await _session()
    try:
        db.add(
            DailyGeneration(
                station_id=sid,
                day=today - timedelta(days=1),
                kwh=123,
                current_kw=42,
                source="forecast",
            )
        )
        await db.commit()
        values = await accumulate.metrics_from_db(db, [sid])
        assert values[sid]["daily"] is None and values[sid]["current"] is None
        assert values[sid]["total"] == 123
        db.add(
            DailyGeneration(
                station_id=sid,
                day=today,
                kwh=234,
                current_kw=88,
                updated_at=(now - timedelta(hours=2)).replace(tzinfo=None),
                source="forecast",
            )
        )
        await db.commit()
        values = await accumulate.metrics_from_db(db, [sid])
        assert values[sid]["daily"] == 234 and values[sid]["current"] is None
        await accumulate.upsert_daily(db, sid, today, 345, 99)
        await db.commit()
        db.expire_all()
        assert (await accumulate.metrics_from_db(db, [sid]))[sid]["current"] == 99
    finally:
        await gen.aclose()


async def test_每天独立覆盖且共同样本只包含七天完整站(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)

    def curves(plants, *_args):
        return [
            (p, [np.full(96, 1.0) if p.id == "a" or k > 0 else None for k in range(7)])
            for p in plants
        ]

    monkeypatch.setattr(fleet, "calculate_cell", curves)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta.json").respond(404)
        mock.get(url__regex=r".*/v1/forecast.*").mock(side_effect=batched(forecast()))
        async with httpx.AsyncClient() as http:
            await fleet.build(http, "gfs_global", fleet.day_key(), [plant("a"), plant("b")])
    out = fleet.load(tmp_path / f"gfs_global-{fleet.day_key()}.json")
    assert out["days"][0]["covered_count"] == 1
    assert out["days"][1]["covered_count"] == 2
    assert out["days"][0]["status"] == "partial"
    assert out["days"][1]["status"] == "ready"
    assert out["common_covered_count"] == 1
    assert all(d["common_energy_kwh"] == 24 for d in out["days"])
    assert out["days"][0]["covered_capacity_kw"] == 1000


def test_单格今日缺测仍计算未来日():
    raw = forecast()
    day = fleet.day_key()
    for i, t in enumerate(raw["minutely_15"]["time"]):
        if t.startswith(day):
            for col in ("wind_speed_10m", "wind_speed_80m", "wind_speed_100m", "wind_speed_120m"):
                raw["minutely_15"][col][i] = None
    result = fleet.calculate_cell([plant("a")], raw, "gfs_global", day, 31.3, 120.6)
    assert len(result) == 1
    assert result[0][1][0] is None and result[0][1][1] is not None


async def test_新批次不复用旧天气且不伪造拉取时间(tmp_path, monkeypatch):
    from app.providers.open_meteo import ModelMeta

    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    issued = datetime.now(UTC) - timedelta(hours=6)
    meta = ModelMeta("ncep_gfs013", issued, issued, 21600)

    async def get_meta(*args, **kwargs):
        return meta

    monkeypatch.setattr(weather, "get_model_meta", get_meta)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*/v1/forecast.*").mock(side_effect=batched(forecast()))
        async with httpx.AsyncClient() as http:
            await fleet.build(http, "gfs_global", fleet.day_key(), [plant("a")])
            path = tmp_path / f"gfs_global-{fleet.day_key()}.json"
            first = fleet.load(path)
            archive_path = (
                tiles.tile_dir().parent
                / "prediction-fleet-inputs"
                / f"{first['input_archive_id']}.json"
            )
            archived = json.loads(archive_path.read_text())
            assert archived["weather_cells"] and archived["calculation_parameters"]
            assert len(archived["plants"][0]["covered_dates"]) == 7
            await fleet.build(http, "gfs_global", fleet.day_key(), [plant("a")])
            assert route.call_count == 1
            assert fleet.load(path)["basis"]["fetched_at"] == first["basis"]["fetched_at"]
            assert fleet.load(path)["input_archive_id"] == first["input_archive_id"]
            meta = ModelMeta("ncep_gfs013", issued + timedelta(hours=6), issued, 21600)
            await fleet.build(http, "gfs_global", fleet.day_key(), [plant("a")])
            assert route.call_count == 2
            assert fleet.load(path)["basis"]["issued_at"] != first["basis"]["issued_at"]
            assert fleet.load(path)["input_archive_id"] != first["input_archive_id"]
            assert json.loads(archive_path.read_text()) == archived


def test_网格或模型参数变化必须改变计算指纹(monkeypatch):
    initial = calculation_version(fleet.day_key())
    monkeypatch.setattr(settings, "fleet_grid_step_wind", 0.5)
    assert calculation_version(fleet.day_key()) != initial


def test_七天留档保存设备与高频输入且不覆盖(tmp_path):
    st = station()
    st.id = "审查站"
    fc = weather.parse_forecast(forecast())
    out = prediction.compute_days(st, fc, 7)
    prediction_archive.save_outlook(st, fc, out)
    out.generated_at = datetime.now(UTC).isoformat()
    prediction_archive.save_outlook(st, fc, out)
    files = list(tmp_path.rglob("prediction-outlook/*/*.json"))
    assert len(files) == 1
    content = json.loads(files[0].read_text())
    assert len(content["prediction"]["days"]) == 7
    assert content["weather_resolution_minutes"] == 15
    assert len(content["weather_input"]["index"]) == len(fc.data)
    assert content["calculation_parameters"]["pv_losses"] == settings.pv_losses
    assert content["station_parameters"]["hub_height"] == 100
    st.hub_height = 120
    prediction_archive.save_outlook(st, fc, out)
    assert len(list(tmp_path.rglob("prediction-outlook/*/*.json"))) == 2


def test_全目录留档同日多次签发都保留(tmp_path):
    out = fleet.blank("gfs_global", fleet.day_key()).model_dump()
    out.update(
        status="ready",
        covered_count=2,
        days=[
            {
                "date": fleet.day_key(),
                "energy_kwh": 24,
                "power_kw": [{"time": "00:00", "value": 1}],
                "covered_count": 1,
            }
        ],
    )
    fleet_history.capture_leads(out)
    out["generated_at"] = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    fleet_history.capture_leads(out)
    files = list(tmp_path.rglob("leads/*/*/*.json"))
    assert len(files) == 2
    assert all(json.loads(p.read_text())["covered_count"] == 1 for p in files)


async def test_统一预算按变量计费并遵循重试时间(monkeypatch):
    from app.providers.budget import RequestBudget, request_cost

    budget = RequestBudget()
    monkeypatch.setattr(settings, "upstream_units_per_minute", 480)
    assert request_cost({"latitude": "31,32", "hourly": ",".join(map(str, range(15)))}) == 3
    await asyncio.gather(*(budget.take(1.5) for _ in range(10)))
    assert sum(cost for _, cost in budget.marks) == 15
    budget.retry_after("120")
    import time

    assert budget.paused_until - time.monotonic() > 119
