"""公开电站目录：搜索 / 附近 / 视野内、地理搜索合并、从目录建站。"""

import pytest
from httpx import AsyncClient

from app.models import CatalogPlant

PLANTS = [
    # id, name, name_local, type, MW, lat, lon, province
    ("wri:A", "Suzhou roofs", None, "solar", 30, 31.45, 120.75, None),
    ("gem:B", "Guazhou Wind Base", "瓜州风电基地", "wind", 200, 40.52, 95.78, "甘肃省"),
    ("wri:C", "Dunhuang PV", None, "solar", 50, 40.14, 94.66, None),
    ("gem:D", "Zhangbei Wind", "张北风电场", "wind", 100, 41.16, 114.70, "河北省"),
]


@pytest.fixture
async def seeded(client: AsyncClient):
    from app.db import get_session
    from app.main import app

    gen = app.dependency_overrides[get_session]()
    db = await gen.__anext__()
    for pid, name, local, t, mw, lat, lon, prov in PLANTS:
        db.add(
            CatalogPlant(
                id=pid,
                source=pid.split(":")[0],
                source_id=pid.split(":")[1],
                name=name,
                name_local=local,
                type=t,
                capacity_kw=mw * 1000,
                latitude=lat,
                longitude=lon,
                province=prov,
            )
        )
    await db.commit()
    yield
    await gen.aclose()


class TestCatalog:
    async def test_附近按距离排序并转坐标(self, client: AsyncClient, seeded):
        r = await client.get(
            "/v1/stations/catalog", params={"near": "31.30,120.62", "coord": "gcj02"}
        )
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["total"] == 4
        assert [p["name"] for p in d["plants"]] == ["Suzhou roofs"]  # 200 km 粗筛内只有它
        p = d["plants"][0]
        assert 15 < p["distance_km"] < 30
        assert p["capacity"] == 30000 and p["source"] == "wri" and p["name_en"] is None
        assert abs(p["longitude"] - 120.75) > 0.001  # 已转 GCJ-02

    async def test_关键词匹配中文名与省份(self, client: AsyncClient, seeded):
        d = (await client.get("/v1/stations/catalog", params={"keyword": "风电"})).json()["data"]
        assert [p["name"] for p in d["plants"]] == ["瓜州风电基地", "张北风电场"]  # 容量降序
        assert d["plants"][0]["name_en"] == "Guazhou Wind Base"
        assert d["plants"][0]["address"] == "甘肃省"
        d = (await client.get("/v1/stations/catalog", params={"keyword": "河北"})).json()["data"]
        assert [p["id"] for p in d["plants"]] == ["gem:D"]

    async def test_视野内按类型筛选(self, client: AsyncClient, seeded):
        d = (
            await client.get(
                "/v1/stations/catalog", params={"bbox": "93,38,98,42", "type": "solar"}
            )
        ).json()["data"]
        assert [p["id"] for p in d["plants"]] == ["wri:C"]
        assert d["total"] == 2  # 该类型总数

    async def test_参数校验(self, client: AsyncClient, seeded):
        r = await client.get("/v1/stations/catalog", params={"near": "abc"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM"
        r = await client.get("/v1/stations/catalog", params={"bbox": "98,38,93,42"})
        assert r.status_code == 400

    async def test_地理搜索合并目录结果(self, client: AsyncClient, seeded):
        d = (await client.get("/v1/geo/search", params={"keyword": "Dunhuang"})).json()["data"]
        plant = [r for r in d["results"] if r["type"] == "plant"]
        assert plant and plant[0]["catalog_id"] == "wri:C"
        assert plant[0]["address"] == "光伏 50 MW"  # 无省市区时用类型+容量顶上
        assert all(r["catalog_id"] is None for r in d["results"] if r["type"] != "plant")

    async def test_从目录建站带溯源(self, client: AsyncClient, seeded):
        body = {
            "name": "瓜州风电基地",
            "type": "wind",
            "latitude": 40.52,
            "longitude": 95.78,
            "capacity": 200000,
            "catalog_id": "gem:B",
        }
        r = await client.post("/v1/stations", json=body)
        assert r.status_code == 201, r.text
        sid = r.json()["data"]["id"]
        from app.db import get_session
        from app.main import app
        from app.models import Station

        gen = app.dependency_overrides[get_session]()
        db = await gen.__anext__()
        s = await db.get(Station, sid)
        assert s.catalog_id == "gem:B"
        await gen.aclose()
