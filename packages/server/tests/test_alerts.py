from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import respx
from httpx import AsyncClient, Response

from app.services import alerts, weather
from app.services.weather import parse_forecast
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


def _with_cloud_drop(raw: dict, hours_ahead: int, factor: float) -> dict:
    """从当前时刻起 hours_ahead 小时后，把辐射乘以 factor（模拟云来了）"""
    now = datetime.now(ZoneInfo(TZ)).replace(minute=0, second=0, microsecond=0)
    t0 = now.replace(tzinfo=None) + timedelta(hours=hours_ahead)
    h = raw["hourly"]
    for i, ts in enumerate(h["time"]):
        if datetime.fromisoformat(ts) >= t0:
            for k in (
                "shortwave_radiation",
                "direct_radiation",
                "diffuse_radiation",
                "direct_normal_irradiance",
            ):
                h[k][i] = round(h[k][i] * factor, 1)
            h["cloud_cover"][i] = 90.0
    return raw


class TestRules:
    """规则层直接测，不走 HTTP。"""

    def _station(self):
        from app.models import Station

        return Station(
            id="s1",
            owner_id="u",
            name="x",
            type="solar",
            latitude=31.3,
            longitude=120.62,
            capacity_kw=500,
        )

    def test_晴天无云层预警(self):
        fc = parse_forecast(make_forecast(start_date=_yesterday_midnight(), peak_today=900))
        now_h = datetime.now(ZoneInfo(TZ)).hour
        found = alerts.detect_all(fc, self._station())
        kinds = {d.kind for d in found}
        if 8 <= now_h <= 14:  # 白天且未来 6h 仍有日照才有意义
            assert "cloud" not in kinds

    def test_辐射骤降触发云层预警并按降幅分级(self):
        now_h = datetime.now(ZoneInfo(TZ)).hour
        if not (7 <= now_h <= 13):
            pytest.skip("规则只在白天且未来 6h 有日照时可测")
        raw = _with_cloud_drop(
            make_forecast(start_date=_yesterday_midnight(), peak_today=900), 2, 0.5
        )
        found = alerts.detect_all(parse_forecast(raw), self._station())
        cloud = next(d for d in found if d.kind == "cloud")
        assert cloud.level in ("moderate", "severe")
        assert "云量增加" in cloud.title and "%" in cloud.title

    def test_强风分级(self):
        raw = make_forecast(start_date=_yesterday_midnight(), wind_today=18.0)
        found = alerts.detect_weather(parse_forecast(raw))
        assert any(d.kind == "wind" and d.level == "moderate" for d in found)
        raw = make_forecast(start_date=_yesterday_midnight(), wind_today=27.0)
        found = alerts.detect_weather(parse_forecast(raw))
        assert any(d.kind == "wind" and d.level == "severe" for d in found)

    def test_高温(self):
        raw = make_forecast(start_date=_yesterday_midnight(), temp_today=40.0)
        found = alerts.detect_weather(parse_forecast(raw))
        assert any(d.kind == "heat" for d in found)

    def test_雷暴(self):
        raw = make_forecast(start_date=_yesterday_midnight(), code_now=0, code_later=95)
        found = alerts.detect_weather(parse_forecast(raw))
        assert any(d.kind == "rain" for d in found)


class TestApi:
    async def _station_with(self, client: AsyncClient, raw: dict) -> str:
        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(return_value=Response(200, json=raw))
            r = await client.post("/v1/stations", json=SUZHOU)
            sid = r.json()["data"]["id"]
            await client.get("/v1/alerts/current", params={"station_id": sid})  # 触发扫描
        return sid

    async def test_强风预警出现在当前与列表(self, client: AsyncClient):
        raw = make_forecast(start_date=_yesterday_midnight(), wind_today=18.0)
        sid = await self._station_with(client, raw)
        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(return_value=Response(200, json=raw))
            cur = (await client.get("/v1/alerts/current", params={"station_id": sid})).json()[
                "data"
            ]
            assert cur["alert"]["level"] == "moderate"
            assert cur["cloud_motion"] is None  # 预报类没有外推
            assert cur["alert"]["published_at"].endswith("+08:00")

            lst = (await client.get("/v1/alerts", params={"station_id": sid})).json()["data"]
            assert len(lst["alerts"]) == 1 and lst["next_cursor"] is None

            # 首页也带上
            home = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]
            assert home["alert"]["level"] == "moderate"

    async def test_等级筛选不含解除(self, client: AsyncClient):
        raw_windy = make_forecast(start_date=_yesterday_midnight(), wind_today=18.0)
        sid = await self._station_with(client, raw_windy)
        # 风停了 → 再扫一次应生成 cleared
        raw_calm = make_forecast(start_date=_yesterday_midnight(), wind_today=3.0)
        weather.clear_cache()
        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(return_value=Response(200, json=raw_calm))
            await client.get("/v1/alerts/current", params={"station_id": sid})
            all_ = (await client.get("/v1/alerts", params={"station_id": sid})).json()["data"][
                "alerts"
            ]
            mod = (
                await client.get("/v1/alerts", params={"station_id": sid, "level": "moderate"})
            ).json()["data"]["alerts"]
            cur = (await client.get("/v1/alerts/current", params={"station_id": sid})).json()[
                "data"
            ]
        levels = [a["level"] for a in all_]
        assert "cleared" in levels and "moderate" in levels
        assert all(a["level"] == "moderate" for a in mod)  # 筛选里没有 cleared
        assert cur["alert"] is None  # 解除后没有生效中的

    async def test_两小时内同类型只更新不新建(self, client: AsyncClient):
        raw = make_forecast(start_date=_yesterday_midnight(), wind_today=18.0)
        sid = await self._station_with(client, raw)
        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(return_value=Response(200, json=raw))
            for _ in range(3):
                await client.get("/v1/alerts/current", params={"station_id": sid})
            lst = (await client.get("/v1/alerts", params={"station_id": sid})).json()["data"][
                "alerts"
            ]
        assert len(lst) == 1

    async def test_非法level(self, client: AsyncClient):
        raw = make_forecast(start_date=_yesterday_midnight())
        sid = await self._station_with(client, raw)
        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(return_value=Response(200, json=raw))
            r = await client.get("/v1/alerts", params={"station_id": sid, "level": "bad"})
        assert r.status_code == 400

    async def test_无站点404(self, client: AsyncClient):
        r = await client.get("/v1/alerts/current")
        assert r.status_code == 404
