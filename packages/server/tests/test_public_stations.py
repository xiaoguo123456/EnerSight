"""共享目录：账号一致、分页完整、直接查看及公共数据写保护。"""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.auth import issue_token
from app.db import get_session
from app.main import app
from app.models import CatalogPlant, Station
from app.services import weather
from tests.test_catalog import seeded  # noqa: F401
from tests.test_home import open_meteo  # noqa: F401

pytestmark = pytest.mark.usefixtures("seeded", "open_meteo")


@pytest.fixture(autouse=True)
def clear_weather():
    weather.clear_cache()
    yield
    weather.clear_cache()


async def test_不同账号无需添加即可看到同一完整目录(client: AsyncClient):
    pages = []
    for user in ("first-user", "second-user"):
        token, _ = issue_token(user)
        headers = {"Authorization": f"Bearer {token}"}
        first = await client.get("/v1/stations/public", params={"limit": 2}, headers=headers)
        second = await client.get(
            "/v1/stations/public", params={"limit": 2, "offset": 2}, headers=headers
        )
        assert first.status_code == second.status_code == 200
        a, b = first.json()["data"], second.json()["data"]
        assert a["counts"] == {"all": 4, "solar": 2, "wind": 2}
        assert a["total"] == b["total"] == 4
        assert a["has_more"] and not b["has_more"]
        ids = [s["id"] for s in a["stations"] + b["stations"]]
        assert len(set(ids)) == 4
        pages.append(ids)
    assert pages[0] == pages[1]
    async for db in app.dependency_overrides[get_session]():
        assert (await db.execute(select(func.count()).select_from(Station))).scalar_one() == 0


async def test_公共目录过滤与边界(client: AsyncClient):
    response = await client.get("/v1/stations/public", params={"type": "wind", "keyword": " 河北 "})
    data = response.json()["data"]
    assert data["total"] == 1 and data["counts"]["all"] == 4
    assert data["stations"][0]["name"] == "张北风电场"
    assert not data["has_more"]
    for params in ({"offset": -1}, {"limit": 101}, {"keyword": "x" * 65}):
        assert (await client.get("/v1/stations/public", params=params)).status_code == 400
    empty = (await client.get("/v1/stations/public", params={"keyword": "%"})).json()["data"]
    assert empty["total"] == 0  # 通配符作为普通关键词，不扩大查询


async def test_直接查看公共电站及默认首页(client: AsyncClient):
    for user in ("first-user", "second-user"):
        token, _ = issue_token(user)
        headers = {"Authorization": f"Bearer {token}"}
        for path, params in (
            ("/v1/home", {}),
            ("/v1/stations/gem:B/detail", {}),
            ("/v1/map/overview", {"station_id": "gem:B"}),
        ):
            response = await client.get(path, params=params, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["data"]["station"]["id"] == "gem:B"
    async for db in app.dependency_overrides[get_session]():
        assert (await db.execute(select(func.count()).select_from(Station))).scalar_one() == 0


async def test_公共电站禁止个人修改删除_退役不可查看(client: AsyncClient):
    response = await client.patch("/v1/stations/gem:B", json={"name": "篡改"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CATALOG_READ_ONLY"
    assert (await client.delete("/v1/stations/gem:B")).status_code == 403
    async for db in app.dependency_overrides[get_session]():
        plant = await db.get(CatalogPlant, "gem:B")
        assert plant.display_name == "瓜州风电基地"
        plant.status = "retired"
        await db.commit()
    data = (await client.get("/v1/stations/public")).json()["data"]
    assert data["total"] == 3
    assert (await client.get("/v1/stations/gem:B/detail")).status_code == 404


async def test_地区筛选与名称排序(client: AsyncClient):
    response = await client.get(
        "/v1/stations/public", params={"province": "甘肃省", "sort": "name"}
    )
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["stations"][0]["id"] == "gem:B"
    assert set(data["regions"]) == {"甘肃省", "河北省"}
    response = await client.get(
        "/v1/stations/public", params={"province": "甘肃省", "type": "solar"}
    )
    assert response.json()["data"]["total"] == 0
    data = (await client.get("/v1/stations/public", params={"sort": "name"})).json()["data"]
    assert [s["id"] for s in data["stations"]] == ["wri:C", "wri:A", "gem:D", "gem:B"]


async def test_预警上下文和报告生成方式(client: AsyncClient):
    response = await client.get("/v1/alerts/current", params={"station_id": "gem:B"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["station"]["id"] == "gem:B" and data["checked_at"]
    response = await client.get("/v1/reports/gem:B")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["method"] == "rule"
