"""地图省域视野必须完整且不依赖公开电站目录。"""

from app.schemas.common import Coord
from app.services.province_bounds import province_bounds


async def test_单省与多省范围覆盖完整边界(client):
    xinjiang = province_bounds("新疆维吾尔自治区", Coord.WGS84)
    assert xinjiang.bounds.sw.longitude < 73.606
    assert xinjiang.bounds.ne.longitude > 96.404
    assert xinjiang.bounds.sw.latitude < 34.356
    assert xinjiang.bounds.ne.latitude > 49.151

    response = await client.get(
        "/v1/geo/province-bounds",
        params={"provinces": "新疆维吾尔自治区,内蒙古自治区", "coord": "gcj02"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provinces"] == ["新疆维吾尔自治区", "内蒙古自治区"]
    assert data["bounds"]["sw"]["longitude"] < 73.606
    assert data["bounds"]["ne"]["longitude"] > 126.108


async def test_未知省份不猜测范围(client):
    response = await client.get("/v1/geo/province-bounds", params={"provinces": "地区待补充"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PARAM"


async def test_目录台湾名称能定位到省域(client):
    response = await client.get("/v1/geo/province-bounds", params={"provinces": "台湾"})
    assert response.status_code == 200
    assert response.json()["data"]["provinces"] == ["台湾省"]
