import math

import pytest
from httpx import AsyncClient

from app.geo import wgs84_to_gcj02

SUZHOU = {
    "name": "苏州光伏站",
    "type": "solar",
    "latitude": 31.30,
    "longitude": 120.62,
    "capacity": 500,
}
YANGJIANG = {
    "name": "广东风电站",
    "type": "wind",
    "latitude": 21.75,
    "longitude": 111.95,
    "capacity": 2000,
}


async def _create(client: AsyncClient, body: dict, **params) -> dict:
    r = await client.post("/v1/stations", json=body, params=params)
    assert r.status_code == 201, r.text
    return r.json()["data"]


class TestCreate:
    async def test_创建并返回信封(self, client: AsyncClient):
        r = await client.post("/v1/stations", json=SUZHOU)
        assert r.status_code == 201
        body = r.json()
        assert body["meta"]["coord"] == "wgs84"
        d = body["data"]
        assert d["name"] == "苏州光伏站"
        assert d["type"] == "solar"
        assert d["status"] == "normal"
        assert d["capacity"] == 500
        # 指标推算前为 null，不是 0。docs/06 §2.5
        assert d["metrics"] == {
            "daily_generation": None,
            "current_power": None,
            "total_generation": None,
            "co2_reduction": None,
            "grid_generation": None,
        }

    async def test_默认坐标系存储与返回一致(self, client: AsyncClient):
        d = await _create(client, SUZHOU)
        assert abs(d["latitude"] - 31.30) < 1e-9
        assert abs(d["longitude"] - 120.62) < 1e-9

    async def test_按gcj02入参会转成wgs84存储(self, client: AsyncClient):
        g_lng, g_lat = wgs84_to_gcj02(120.62, 31.30)
        body = {**SUZHOU, "latitude": g_lat, "longitude": g_lng, "coord": "gcj02"}
        d = await _create(client, body)  # 出参默认 wgs84
        assert abs(d["latitude"] - 31.30) < 1e-6
        assert abs(d["longitude"] - 120.62) < 1e-6

    async def test_出参按coord转换(self, client: AsyncClient):
        d = await _create(client, SUZHOU, coord="gcj02")
        g_lng, g_lat = wgs84_to_gcj02(120.62, 31.30)
        assert abs(d["latitude"] - g_lat) < 1e-9
        assert abs(d["longitude"] - g_lng) < 1e-9
        # 偏移确实存在（几百米），说明不是原样返回
        assert math.hypot(d["longitude"] - 120.62, d["latitude"] - 31.30) > 1e-3

    @pytest.mark.parametrize(
        ("bad", "field"),
        [
            ({"capacity": 0}, "capacity"),
            ({"capacity": -1}, "capacity"),
            ({"latitude": 91}, "latitude"),
            ({"name": "  "}, "name"),
            ({"type": "storage"}, "type"),
        ],
    )
    async def test_参数校验走统一错误格式(self, client: AsyncClient, bad, field):
        r = await client.post("/v1/stations", json={**SUZHOU, **bad})
        assert r.status_code == 400
        err = r.json()["error"]
        assert err["code"] == "INVALID_PARAM"
        assert field in err["message"]


class TestList:
    async def test_空列表计数为零(self, client: AsyncClient):
        r = await client.get("/v1/stations")
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["stations"] == []
        assert d["counts"] == {"all": 0, "solar": 0, "wind": 0}

    async def test_计数对全量而非筛选结果(self, client: AsyncClient):
        await _create(client, SUZHOU)
        await _create(client, YANGJIANG)
        r = await client.get("/v1/stations", params={"type": "solar"})
        d = r.json()["data"]
        assert len(d["stations"]) == 1
        assert d["stations"][0]["type"] == "solar"
        # 筛了 solar，但 counts 仍是全量 —— 前端 Tab 的数字要对
        assert d["counts"] == {"all": 2, "solar": 1, "wind": 1}

    async def test_列表坐标按coord转换(self, client: AsyncClient):
        await _create(client, SUZHOU)
        r = await client.get("/v1/stations", params={"coord": "gcj02"})
        assert r.json()["meta"]["coord"] == "gcj02"
        s = r.json()["data"]["stations"][0]
        g_lng, g_lat = wgs84_to_gcj02(120.62, 31.30)
        assert abs(s["longitude"] - g_lng) < 1e-9


class TestUpdateDelete:
    async def test_部分更新(self, client: AsyncClient):
        d = await _create(client, SUZHOU)
        r = await client.patch(
            f"/v1/stations/{d['id']}", json={"name": "苏州一期", "capacity": 800}
        )
        assert r.status_code == 200
        u = r.json()["data"]
        assert u["name"] == "苏州一期"
        assert u["capacity"] == 800
        assert u["type"] == "solar"  # 未传的字段不变

    async def test_更新经纬度按coord转换(self, client: AsyncClient):
        d = await _create(client, SUZHOU)
        g_lng, g_lat = wgs84_to_gcj02(121.47, 31.23)
        r = await client.patch(
            f"/v1/stations/{d['id']}",
            json={"latitude": g_lat, "longitude": g_lng, "coord": "gcj02"},
        )
        u = r.json()["data"]
        assert abs(u["latitude"] - 31.23) < 1e-6
        assert abs(u["longitude"] - 121.47) < 1e-6

    async def test_删除后不存在(self, client: AsyncClient):
        d = await _create(client, SUZHOU)
        r = await client.delete(f"/v1/stations/{d['id']}")
        assert r.status_code == 204
        r = await client.patch(f"/v1/stations/{d['id']}", json={"name": "x"})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "STATION_NOT_FOUND"

    async def test_不存在的站点(self, client: AsyncClient):
        r = await client.delete("/v1/stations/nope")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "STATION_NOT_FOUND"


class TestAuth:
    async def test_开发态登录签发token(self, client: AsyncClient):
        r = await client.post("/v1/auth/login", json={"code": "whatever"})
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["token"] and d["expires_in"] > 0

    async def test_带token访问(self, client: AsyncClient):
        token = (await client.post("/v1/auth/login", json={"code": "x"})).json()["data"]["token"]
        r = await client.get("/v1/stations", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    async def test_非开发态无token拒绝(self, client: AsyncClient, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "debug", False)
        r = await client.get("/v1/stations")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHORIZED"

    async def test_坏token(self, client: AsyncClient):
        r = await client.get("/v1/stations", headers={"Authorization": "Bearer garbage"})
        assert r.status_code == 401

    async def test_跨用户不可见(self, client: AsyncClient, monkeypatch):
        from app import auth

        d = await _create(client, SUZHOU)  # dev-user 创建
        # 另一个用户的 token
        other, _ = auth.issue_token("someone-else")
        r = await client.get("/v1/stations", headers={"Authorization": f"Bearer {other}"})
        assert r.json()["data"]["stations"] == []
        r = await client.delete(
            f"/v1/stations/{d['id']}", headers={"Authorization": f"Bearer {other}"}
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "STATION_FORBIDDEN"
