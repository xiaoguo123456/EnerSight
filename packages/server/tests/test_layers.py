from datetime import UTC, datetime

import pytest
import respx
from httpx import AsyncClient, Response

from app.render import grid, tiles
from app.render.colormap import SCALES


def _grid_response(n_points: int) -> list[dict]:
    """合成 Open-Meteo 多点响应：每点 24 小时"""
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    times = [f"{day}T{h:02d}:00" for h in range(24)]
    out = []
    for i in range(n_points):
        out.append(
            {
                "latitude": 0,
                "longitude": 0,
                "timezone": "UTC",
                "hourly": {
                    "time": times,
                    "shortwave_radiation": [float(i % 7) * 100 for _ in times],
                    "temperature_2m": [20.0 + i % 5 for _ in times],
                    "wind_speed_10m": [3.0 for _ in times],
                    "wind_direction_10m": [270.0 for _ in times],
                    "cloud_cover": [40.0 for _ in times],
                },
            }
        )
    return out


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    grid.clear_cache()
    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    yield
    grid.clear_cache()


@pytest.fixture
def open_meteo():
    with respx.mock(assert_all_called=False) as mock:
        yield mock.get(url__regex=r".*open-meteo.*").mock(
            return_value=Response(200, json=_grid_response(grid.N * grid.N))
        )


class TestBlocks:
    def test_块对齐到4度整数倍(self):
        b = grid.Block.containing(31.3, 120.62)
        assert (b.lat0, b.lon0) == (28.0, 120.0)
        assert (b.lat1, b.lon1) == (32.0, 124.0)

    def test_bbox跨块(self):
        bs = grid.blocks_for_bbox(119.5, 30.5, 121.5, 32.5)
        assert len(bs) == 4
        assert {(b.lat0, b.lon0) for b in bs} == {(28, 116), (28, 120), (32, 116), (32, 120)}

    def test_负坐标(self):
        b = grid.Block.containing(-33.87, -70.67)
        assert (b.lat0, b.lon0) == (-36.0, -72.0)


class TestColormap:
    def test_色阶插值与透明(self):
        import numpy as np

        s = SCALES["radiation"]
        rgba = s.rgba(np.array([[0.0, 1000.0, np.nan]]))
        assert tuple(rgba[0, 0, :3]) == (0x3B, 0x5B, 0xDB)  # 最低档色
        assert tuple(rgba[0, 1, :3]) == (0xF0, 0x3E, 0x3E)  # 最高档色
        assert rgba[0, 2, 3] == 0  # NaN 透明
        assert rgba[0, 0, 3] == 190


class TestApi:
    async def test_共用域名的图片地址保留业务前缀(self, client, open_meteo, monkeypatch):
        from app.main import app

        monkeypatch.setattr(app, "root_path", "/enersight")
        response = await client.get(
            "https://platform.qhzhiyin.com/v1/map/layers/radiation",
            params={"bbox": "120.5,28.5,121.5,29.5"},
        )
        assert response.status_code == 200
        image = response.json()["data"]["frames"][0]["images"][0]
        assert image["url"].startswith("https://platform.qhzhiyin.com/enersight/tiles/radiation/")

    async def test_返回块与图例并落盘(self, client: AsyncClient, open_meteo):
        r = await client.get(
            "/v1/map/layers/radiation",
            params={"bbox": "120.5,28.5,121.5,29.5", "zoom": 8, "coord": "gcj02"},
        )
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["layer"] == "radiation" and d["unit"] == "W/m²"
        assert d["legend"]["stops"] == [0, 200, 400, 600, 800, 1000]
        assert len(d["frames"]) == 1
        imgs = d["frames"][0]["images"]
        assert len(imgs) == 1  # bbox 落在单块内
        assert imgs[0]["url"].startswith("http://test/tiles/radiation/")
        # bounds 是块边界（对齐后），不是请求 bbox；且已转 GCJ-02（有偏移）
        sw = imgs[0]["bounds"]["sw"]
        assert abs(sw["latitude"] - 28.0) < 0.01 and sw["latitude"] != 28.0
        # 图片已落盘且是合法 PNG（静态服务是 Starlette 的，不在此测）
        rel = imgs[0]["url"].split("/tiles/")[1]
        png = (tiles.tile_dir() / rel).read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_同块只回源一次(self, client: AsyncClient, open_meteo):
        await client.get("/v1/map/layers/radiation", params={"bbox": "120.5,28.5,121.5,29.5"})
        await client.get("/v1/map/layers/temperature", params={"bbox": "120.5,28.5,121.5,29.5"})
        assert open_meteo.call_count == 1  # 四个场同一次请求拉回

    async def test_云图图例无刻度有标签(self, client: AsyncClient, open_meteo):
        d = (
            await client.get("/v1/map/layers/cloud", params={"bbox": "120.5,28.5,121.5,29.5"})
        ).json()["data"]
        assert d["legend"]["stops"] is None and d["legend"]["labels"] == ["低", "高"]
        assert d["unit"] is None

    async def test_bbox非法(self, client: AsyncClient, open_meteo):
        r = await client.get("/v1/map/layers/radiation", params={"bbox": "1,2,3"})
        assert r.status_code == 400
        r = await client.get("/v1/map/layers/radiation", params={"bbox": "100,20,140,60"})
        assert r.status_code == 400 and "过大" in r.json()["error"]["message"]

    async def test_限流转冷却状态(self, client: AsyncClient):
        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(return_value=Response(429))
            r = await client.get(
                "/v1/map/layers/radiation", params={"bbox": "120.5,28.5,121.5,29.5"}
            )
        assert r.status_code == 429 and "冷却" in r.json()["error"]["message"]


async def test_大视野全覆盖而不是截断六块(client, open_meteo):
    response = await client.get("/v1/map/layers/radiation", params={"bbox": "80,34,94,51"})
    assert response.status_code == 200
    images = response.json()["data"]["frames"][0]["images"]
    assert len(images) <= 4
    assert min(i["bounds"]["sw"]["longitude"] for i in images) <= 80
    assert max(i["bounds"]["ne"]["longitude"] for i in images) >= 94
    assert max(i["bounds"]["ne"]["latitude"] for i in images) >= 51


async def test_风矢量遵循气象来向约定(client, open_meteo):
    response = await client.get("/v1/map/layers/wind", params={"bbox": "120.5,28.5,121.5,29.5"})
    vectors = response.json()["data"]["wind_vectors"]
    assert len(vectors) == grid.N**2
    assert all(v["u"] == 3 and abs(v["v"]) < 0.001 for v in vectors)


async def test_限流冷却避免重复回源(client):
    with respx.mock as mock:
        route = mock.get(url__regex=r".*open-meteo.*").mock(return_value=Response(429))
        for box in ["120.5,28.5,121.5,29.5", "116.5,28.5,117.5,29.5"]:
            response = await client.get("/v1/map/layers/wind", params={"bbox": box})
            assert response.status_code == 429
        assert route.call_count == 1


async def test_重启缓存复用持久化网格(client, open_meteo):
    await client.get("/v1/map/layers/wind", params={"bbox": "120.5,28.5,121.5,29.5"})
    grid.clear_cache()
    await client.get("/v1/map/layers/temperature", params={"bbox": "120.5,28.5,121.5,29.5"})
    assert open_meteo.call_count == 1


async def test_放大视野仍有插值数值且无额外回源(client, open_meteo):
    response = await client.get(
        "/v1/map/layers/temperature", params={"bbox": "120.51,28.51,120.61,28.61"}
    )
    samples = response.json()["data"]["samples"]
    assert len(samples) == 9
    assert all(120.51 < p["longitude"] < 120.61 and 28.51 < p["latitude"] < 28.61 for p in samples)
    assert all(20.5 <= p["value"] <= 20.6 for p in samples)
    assert open_meteo.call_count == 1


async def test_地图内存磁盘和图片按模型隔离(client, open_meteo):
    paths = []
    for model in ["ecmwf_ifs", "gfs_global", "ecmwf_ifs"]:
        grid.clear_cache()
        r = await client.get(
            "/v1/map/layers/temperature",
            params={"bbox": "120.5,28.5,121.5,29.5"},
            headers={"X-Weather-Model": model},
        )
        assert r.status_code == 200
        paths.append(r.json()["data"]["frames"][0]["images"][0]["url"])
    assert paths[0] == paths[2] and paths[0] != paths[1]
    assert open_meteo.call_count == 2
    assert {c.request.url.params["models"] for c in open_meteo.calls} == {"ecmwf_ifs", "gfs_global"}


async def test_云图部分块拿不到卫星时整层退回预报(client, open_meteo, monkeypatch):
    """卫星是亮度拉伸的相对强度、预报是云量百分比，混在一个响应里同一个图例解释不了"""
    from app.services import layers as svc

    seen = []

    async def only_first(_http, block, _base_url):
        seen.append(block.lon0)
        return (
            ("http://x/tiles/cloud-sat/a.png", "2026-09-09T04:00", False)
            if len(seen) == 1
            else None
        )

    monkeypatch.setattr(svc, "_ensure_satellite_tile", only_first)
    d = (await client.get("/v1/map/layers/cloud", params={"bbox": "118.5,28.5,125.5,29.5"})).json()[
        "data"
    ]
    assert len(seen) >= 2  # 确实跨了多块
    assert d["legend"]["title"] == "云量预报"
    urls = [i["url"] for f in d["frames"] for i in f["images"]]
    assert urls and all("cloud-sat" not in u for u in urls)


async def test_云图全部块拿到卫星时用实况图例(client, open_meteo, monkeypatch):
    from app.services import layers as svc

    async def always(_http, block, _base_url):
        return (f"http://x/tiles/cloud-sat/{block.lon0}.png", "2026-09-09T04:00", False)

    monkeypatch.setattr(svc, "_ensure_satellite_tile", always)
    d = (await client.get("/v1/map/layers/cloud", params={"bbox": "118.5,28.5,125.5,29.5"})).json()[
        "data"
    ]
    assert d["legend"]["title"] == SCALES["cloud"].title
    assert all("cloud-sat" in i["url"] for f in d["frames"] for i in f["images"])


class TestCurrentHourIndex:
    """辐射是区间均值量，其余图层是瞬时量，「当前」不是同一格。docs/04 §二"""

    def _freeze(self, monkeypatch, hour: int, minute: int):
        from app.services import layers as svc

        fixed = datetime(2026, 9, 9, hour, minute, tzinfo=UTC)

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr(svc, "datetime", _Clock)
        return svc, [f"2026-09-09T{h:02d}:00" for h in range(24)]

    def test_辐射取包含当前时刻的区间其余取整点(self, monkeypatch):
        svc, times = self._freeze(monkeypatch, 6, 30)
        assert svc._current_hour_index(times, "radiation") == 7
        assert svc._current_hour_index(times, "temperature") == 6
        assert svc._current_hour_index(times) == 6

    def test_整点时两者相同(self, monkeypatch):
        svc, times = self._freeze(monkeypatch, 6, 0)
        assert svc._current_hour_index(times, "radiation") == 6

    def test_日末使用次日首格_缺失不能回退(self, monkeypatch):
        svc, times = self._freeze(monkeypatch, 23, 30)
        from app.errors import DataUnavailable

        with pytest.raises(DataUnavailable):
            svc._current_hour_index(times, "radiation")
        times.append("2026-09-10T00:00")
        assert svc._current_hour_index(times, "radiation") == 24
