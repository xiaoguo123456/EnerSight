import pytest
import respx
from httpx import AsyncClient, Response

from app.config import settings
from app.geo import wgs84_to_gcj02
from app.services import geo

SUZHOU = {
    "name": "苏州光伏站",
    "type": "solar",
    "latitude": 31.30,
    "longitude": 120.62,
    "capacity": 500,
}


class TestParseCoordinate:
    @pytest.mark.parametrize(
        ("text", "want"),
        [
            ("31.30,120.62", (31.30, 120.62)),
            ("31.30 120.62", (31.30, 120.62)),
            ("31.30，120.62", (31.30, 120.62)),
            ("120.62, 31.30", (31.30, 120.62)),  # 写反自动纠正
            ("abc", None),
            ("95, 120", None),
        ],
    )
    def test_解析(self, text, want):
        assert geo.parse_coordinate(text) == want


class TestNoKey:
    """未配置腾讯 key 时的降级：逆地理返回 503，搜索只有站点与坐标。"""

    async def test_搜索站点与坐标(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr(settings, "tencent_lbs_key", "")
        await client.post("/v1/stations", json=SUZHOU)
        r = await client.get("/v1/geo/search", params={"keyword": "苏州"})
        types = [x["type"] for x in r.json()["data"]["results"]]
        assert types == ["station"]

        r = await client.get("/v1/geo/search", params={"keyword": "31.3, 120.62", "coord": "gcj02"})
        res = r.json()["data"]["results"]
        assert res[0]["type"] == "coordinate"
        g_lng, g_lat = wgs84_to_gcj02(120.62, 31.3)
        assert abs(res[0]["longitude"] - g_lng) < 1e-6  # 出参按 coord 转换

    async def test_逆地理无key返回503(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr(settings, "tencent_lbs_key", "")
        r = await client.get("/v1/geo/reverse", params={"latitude": 31.3, "longitude": 120.62})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "DATA_UNAVAILABLE"

    async def test_建站时地址为空不报错(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr(settings, "tencent_lbs_key", "")
        r = await client.post("/v1/stations", json=SUZHOU)
        assert r.status_code == 201 and r.json()["data"]["address"] is None


class TestWithKey:
    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        monkeypatch.setattr(settings, "tencent_lbs_key", "test-key")
        geo._reverse_cache.clear()

    async def test_逆地理编码入参转gcj02出参组装地址(self, client: AsyncClient):
        with respx.mock:
            route = respx.get(url__regex=r".*apis\.map\.qq\.com/ws/geocoder.*").mock(
                return_value=Response(
                    200,
                    json={
                        "status": 0,
                        "result": {
                            "address_component": {
                                "province": "江苏省",
                                "city": "苏州市",
                                "district": "吴中区",
                            }
                        },
                    },
                )
            )
            r = await client.get("/v1/geo/reverse", params={"latitude": 31.3, "longitude": 120.62})
        assert r.status_code == 200
        assert r.json()["data"]["address"] == "江苏省苏州市吴中区"
        # 发给腾讯的必须是 GCJ-02
        loc = route.calls[0].request.url.params["location"]
        g_lng, g_lat = wgs84_to_gcj02(120.62, 31.3)
        lat_s, lng_s = loc.split(",")
        assert abs(float(lat_s) - g_lat) < 1e-6 and abs(float(lng_s) - g_lng) < 1e-6

    async def test_建站自动填地址(self, client: AsyncClient):
        with respx.mock:
            respx.get(url__regex=r".*geocoder.*").mock(
                return_value=Response(
                    200,
                    json={
                        "status": 0,
                        "result": {
                            "address_component": {
                                "province": "江苏省",
                                "city": "苏州市",
                                "district": "吴中区",
                            }
                        },
                    },
                )
            )
            r = await client.post("/v1/stations", json=SUZHOU)
        assert r.json()["data"]["address"] == "江苏省苏州市吴中区"

    async def test_搜索合并POI并转回wgs84(self, client: AsyncClient):
        g_lng, g_lat = wgs84_to_gcj02(120.62, 31.3)
        with respx.mock:
            respx.get(url__regex=r".*place/v1/suggestion.*").mock(
                return_value=Response(
                    200,
                    json={
                        "status": 0,
                        "data": [
                            {
                                "title": "苏州市",
                                "address": "江苏省",
                                "location": {"lat": g_lat, "lng": g_lng},
                            }
                        ],
                    },
                )
            )
            r = await client.get("/v1/geo/search", params={"keyword": "苏州"})
        res = r.json()["data"]["results"]
        poi = next(x for x in res if x["type"] == "poi")
        assert abs(poi["latitude"] - 31.3) < 1e-6  # 腾讯给的 GCJ-02 已转回 WGS84

    async def test_腾讯业务错误按无数据处理(self, client: AsyncClient):
        with respx.mock:
            respx.get(url__regex=r".*geocoder.*").mock(
                return_value=Response(200, json={"status": 110, "message": "key 无效"})
            )
            r = await client.get("/v1/geo/reverse", params={"latitude": 31.3, "longitude": 120.62})
        assert r.status_code == 503

    async def test_腾讯挂了返回502(self, client: AsyncClient):
        with respx.mock:
            respx.get(url__regex=r".*geocoder.*").mock(return_value=Response(500))
            r = await client.get("/v1/geo/reverse", params={"latitude": 31.3, "longitude": 120.62})
        assert r.status_code == 502
