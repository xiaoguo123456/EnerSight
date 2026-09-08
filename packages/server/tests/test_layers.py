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

    async def test_限流转502(self, client: AsyncClient):
        with respx.mock:
            respx.get(url__regex=r".*open-meteo.*").mock(return_value=Response(429))
            r = await client.get(
                "/v1/map/layers/radiation", params={"bbox": "120.5,28.5,121.5,29.5"}
            )
        assert r.status_code == 502 and "限流" in r.json()["error"]["message"]
