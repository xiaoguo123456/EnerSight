from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import respx
from httpx import AsyncClient, Response

from app.services import weather
from tests.fixtures_forecast import TZ, make_forecast

SUZHOU = {
    "name": "苏州光伏站",
    "type": "solar",
    "latitude": 31.30,
    "longitude": 120.62,
    "capacity": 500,
}
WIND = {
    "name": "广东风电站",
    "type": "wind",
    "latitude": 21.75,
    "longitude": 111.95,
    "capacity": 2000,
}


def _yesterday_midnight() -> datetime:
    now = datetime.now(ZoneInfo(TZ))
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


@pytest.fixture(autouse=True)
def _fresh_cache():
    weather.clear_cache()
    yield
    weather.clear_cache()


@pytest.fixture
def open_meteo(**overrides):
    """拦截 Open-Meteo，返回合成预报。用 respx 的 route 可以断言调用次数。"""
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r"https://api\.open-meteo\.com/v1/forecast.*").mock(
            return_value=Response(200, json=make_forecast(start_date=_yesterday_midnight()))
        )
        yield route


async def _create(client: AsyncClient, body: dict) -> str:
    r = await client.post("/v1/stations", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


class TestHomeNoStation:
    async def test_无站点(self, client: AsyncClient, open_meteo):
        r = await client.get("/v1/home")
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["has_station"] is False
        assert d["station"] is None and d["index"] is None and d["weather"] is None
        assert not open_meteo.called  # 没站点不该去拉气象


class TestHomeSolar:
    async def test_聚合响应结构(self, client: AsyncClient, open_meteo):
        sid = await _create(client, SUZHOU)
        r = await client.get("/v1/home", params={"station_id": sid, "coord": "gcj02"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["has_station"] is True
        assert d["station"]["id"] == sid
        assert r.json()["meta"]["coord"] == "gcj02"
        # 契约要求的字段都在，缺失用 null 不省略
        for k in ("index", "weather", "trends", "alert"):
            assert k in d
        assert d["alert"] is None  # 预警系统未接入，按契约 null

    async def test_未指定站点取最早创建的(self, client: AsyncClient, open_meteo):
        first = await _create(client, SUZHOU)
        await _create(client, WIND)
        r = await client.get("/v1/home")
        assert r.json()["data"]["station"]["id"] == first

    async def test_指数与归因(self, client: AsyncClient, open_meteo):
        sid = await _create(client, SUZHOU)
        d = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]
        idx = d["index"]
        assert idx["score"] is not None and 0 <= idx["score"] <= 100
        assert idx["level"] in ("excellent", "good", "fair", "poor")
        assert idx["summary"]  # 规则模板兜底，不能为空
        assert idx["estimated"] is False
        factors = {a["factor"] for a in idx["attribution"]}
        assert {"radiation", "temperature"} <= factors

    async def test_环比对昨日同期(self, client: AsyncClient, open_meteo):
        sid = await _create(client, SUZHOU)
        d = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]
        w = d["weather"]
        # 合成数据：今日风速 4.5 / 昨日 3.0 → +50%
        assert w["wind_speed"]["value"] == 4.5
        assert w["wind_speed"]["delta_percent"] == 50.0
        # 云量 30 / 50 → -40%
        assert w["cloud_cover"]["value"] == 30.0
        assert w["cloud_cover"]["delta_percent"] == -40.0

    async def test_夜间辐射为零时环比为null不是无穷(self, client: AsyncClient, monkeypatch):
        """昨值为 0 时不能除，返回 null 让前端隐藏。docs/07 §3.3"""
        sid = await _create(client, SUZHOU)
        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(
                return_value=Response(
                    200, json=make_forecast(start_date=_yesterday_midnight(), peak_yesterday=0.0)
                )
            )
            d = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]
        assert d["weather"]["radiation"]["delta_percent"] is None

    async def test_趋势25点固定纵轴(self, client: AsyncClient, open_meteo):
        sid = await _create(client, SUZHOU)
        t = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]["trends"]
        assert t["metric"] == "radiation" and t["unit"] == "W/m²" and t["y_max"] == 1000.0
        assert len(t["points"]) == 25
        assert t["points"][0]["time"].endswith("T00:00:00+08:00")
        assert t["points"][-1]["time"].endswith("T00:00:00+08:00")
        # 夜间 0，正午最高
        vals = [p["value"] for p in t["points"]]
        assert vals[0] == 0.0 and max(vals) == vals[12]

    async def test_天气文案转换(self, client: AsyncClient, open_meteo):
        sid = await _create(client, SUZHOU)
        w = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]["weather"]
        # 合成数据 14 点前 code 0（晴）、之后 2（多云）；当前时刻不同结果不同，只断言合法
        assert w["weather_text"] in ("晴", "多云", "晴转多云", "多云转晴")

    async def test_站点指标由气象推算(self, client: AsyncClient, open_meteo):
        sid = await _create(client, SUZHOU)
        m = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]["station"][
            "metrics"
        ]
        assert m["daily_generation"] is not None and m["daily_generation"] > 0
        assert m["daily_generation"] < 500 * 24  # 不可能超过满发
        # 累计需要逐日历史，尚未接入
        assert m["total_generation"] is None and m["co2_reduction"] is None


class TestHomeWind:
    async def test_风电指数(self, client: AsyncClient, open_meteo):
        sid = await _create(client, WIND)
        d = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]
        idx = d["index"]
        assert idx["score"] is not None
        # 4.5 m/s 外推到 85m 约 6 m/s，在爬坡段，CF 偏低 → 不会是优秀
        assert idx["level"] in ("fair", "poor", "good")
        assert d["station"]["metrics"]["daily_generation"] > 0


class TestCache:
    async def test_同网格只回源一次(self, client: AsyncClient, open_meteo):
        sid = await _create(client, SUZHOU)
        await client.get("/v1/home", params={"station_id": sid})
        await client.get("/v1/home", params={"station_id": sid})
        await client.get("/v1/trends", params={"station_id": sid, "metric": "cloud_cover"})
        assert open_meteo.call_count == 1

    async def test_上游故障返回502(self, client: AsyncClient):
        sid = await _create(client, SUZHOU)
        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(return_value=Response(500))
            r = await client.get("/v1/home", params={"station_id": sid})
        assert r.status_code == 502
        assert r.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


class TestTrends:
    async def test_切换指标(self, client: AsyncClient, open_meteo):
        sid = await _create(client, SUZHOU)
        r = await client.get("/v1/trends", params={"station_id": sid, "metric": "wind_speed"})
        t = r.json()["data"]
        assert t["metric"] == "wind_speed" and t["unit"] == "m/s"
        assert t["y_max"] is None  # 风速自适应
        assert all(p["value"] == 4.5 for p in t["points"])

    async def test_非法指标走统一错误(self, client: AsyncClient, open_meteo):
        sid = await _create(client, SUZHOU)
        r = await client.get("/v1/trends", params={"station_id": sid, "metric": "humidity"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_PARAM"
