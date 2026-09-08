from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import DailyGeneration
from app.services import accumulate, weather
from tests.fixtures_forecast import TZ, make_forecast

SUZHOU = {
    "name": "苏州光伏站",
    "type": "solar",
    "latitude": 31.30,
    "longitude": 120.62,
    "capacity": 500,
}


def _yesterday_midnight() -> datetime:
    now = datetime.now(ZoneInfo(TZ))
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


@pytest.fixture(autouse=True)
def _fresh():
    weather.clear_cache()
    yield
    weather.clear_cache()


@pytest.fixture
def open_meteo():
    with respx.mock(assert_all_called=False) as mock:
        yield mock.get(url__regex=r".*open-meteo.*").mock(
            return_value=Response(200, json=make_forecast(start_date=_yesterday_midnight()))
        )


async def _session():
    """拿测试库的 session（复用 conftest 注入的 override）"""
    gen = app.dependency_overrides[__import__("app.db", fromlist=["get_session"]).get_session]()
    return await gen.__anext__(), gen


class TestAccumulate:
    async def test_首次无记录时累计为null(self, client: AsyncClient, open_meteo):
        r = await client.post("/v1/stations", json=SUZHOU)
        sid = r.json()["data"]["id"]
        d = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]
        assert d["station"]["metrics"]["total_generation"] is None
        assert d["station"]["metrics"]["co2_reduction"] is None

    async def test_跑一次任务后有累计(self, client: AsyncClient, open_meteo):
        r = await client.post("/v1/stations", json=SUZHOU)
        sid = r.json()["data"]["id"]

        db, gen = await _session()
        try:
            n = await accumulate.accumulate_all(db, app.state.http)
            assert n == 1
            rows = (await db.execute(select(DailyGeneration))).scalars().all()
            assert len(rows) == 1
            assert rows[0].station_id == sid and rows[0].kwh > 0
            assert rows[0].day == date.today() or abs((rows[0].day - date.today()).days) <= 1
        finally:
            await gen.aclose()

        d = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]
        m = d["station"]["metrics"]
        assert m["total_generation"] == pytest.approx(rows[0].kwh, abs=0.2)
        # 减排 = 累计 × 排放因子。docs/07 §3.1
        assert m["co2_reduction"] == pytest.approx(
            rows[0].kwh * settings.co2_factor_kg_per_kwh, rel=1e-3
        )

    async def test_同一天重复跑是覆盖不是追加(self, client: AsyncClient, open_meteo):
        await client.post("/v1/stations", json=SUZHOU)
        db, gen = await _session()
        try:
            await accumulate.accumulate_all(db, app.state.http)
            await accumulate.accumulate_all(db, app.state.http)
            rows = (await db.execute(select(DailyGeneration))).scalars().all()
            assert len(rows) == 1
        finally:
            await gen.aclose()

    async def test_实测值不被推算覆盖(self, client: AsyncClient, open_meteo):
        r = await client.post("/v1/stations", json=SUZHOU)
        r.json()["data"]["id"]
        db, gen = await _session()
        try:
            await accumulate.accumulate_all(db, app.state.http)
            row = (await db.execute(select(DailyGeneration))).scalars().one()
            row.kwh, row.source = 9999.0, "measured"
            await db.commit()
            await accumulate.accumulate_all(db, app.state.http)
            row = (await db.execute(select(DailyGeneration))).scalars().one()
            assert row.kwh == 9999.0 and row.source == "measured"
        finally:
            await gen.aclose()

    async def test_单站失败不影响其他站(self, client: AsyncClient, monkeypatch):
        await client.post("/v1/stations", json=SUZHOU)
        await client.post(
            "/v1/stations", json={**SUZHOU, "name": "坏站", "latitude": 0.5, "longitude": 0.5}
        )
        calls = {"n": 0}

        def _forecast_for(latitude, longitude):
            calls["n"] += 1
            if latitude < 1:
                return Response(500)
            return Response(200, json=make_forecast(start_date=_yesterday_midnight()))

        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(
                side_effect=lambda req: _forecast_for(
                    float(req.url.params["latitude"]), float(req.url.params["longitude"])
                )
            )
            db, gen = await _session()
            try:
                n = await accumulate.accumulate_all(db, app.state.http)
            finally:
                await gen.aclose()
        assert n == 1  # 坏站失败，好站成功


class TestListMetrics:
    async def test_列表指标来自累积表(self, client: AsyncClient, open_meteo):
        r = await client.post("/v1/stations", json=SUZHOU)
        r.json()["data"]["id"]
        # 任务未跑：全 null
        m0 = (await client.get("/v1/stations")).json()["data"]["stations"][0]["metrics"]
        assert all(v is None for v in m0.values())

        db, gen = await _session()
        try:
            await accumulate.accumulate_all(db, app.state.http)
        finally:
            await gen.aclose()

        m = (await client.get("/v1/stations")).json()["data"]["stations"][0]["metrics"]
        assert m["daily_generation"] > 0
        assert m["total_generation"] == m["daily_generation"]  # 只有一天
        assert m["co2_reduction"] == pytest.approx(
            m["total_generation"] * settings.co2_factor_kg_per_kwh, rel=1e-3
        )
        assert m["current_power"] is not None
