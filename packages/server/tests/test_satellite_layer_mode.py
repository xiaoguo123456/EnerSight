"""首页卫星入口必须拿真实、同一时刻的卫星帧。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np

from app.errors import UpstreamUnavailable
from app.satellite.reproject import Reprojected


async def test_大省视野返回真实卫星帧(client, monkeypatch, tmp_path):
    from app.render import tiles
    from app.services import layers

    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    layers._satellite_images.clear()
    observed = datetime(2026, 9, 23, 4, 20, tzinfo=UTC)
    calls = []

    async def latest(_http):
        return observed

    async def mosaic(_http, band, bbox):
        calls.append((band, bbox))
        return SimpleNamespace(observed_at=observed)

    def project(_mosaic, bbox, _size):
        rgb = np.full((4, 4, 3), 120, dtype=np.uint8)
        return Reprojected(rgb=rgb, gray=rgb[..., 0], bbox=bbox)

    monkeypatch.setattr(layers.himawari, "latest_time", latest)
    monkeypatch.setattr(layers.himawari, "fetch_latest_mosaic", mosaic)
    monkeypatch.setattr(layers.satellite, "analysis_band", lambda *_: "visible")
    monkeypatch.setattr(layers, "reproject", project)

    response = await client.get(
        "/v1/map/layers/cloud",
        params={"bbox": "92,36,128,56", "source": "satellite", "coord": "gcj02"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["source"] == "日本气象厅 / JMA Himawari-9"
    assert data["observed_at"] == "2026-09-23T04:20+00:00"
    assert data["coverage"] == "真彩影像"
    assert len(data["frames"][0]["images"]) == 1
    assert "/tiles/cloud-image/" in data["frames"][0]["images"][0]["url"]
    assert calls[0][0] == "truecolor"


async def test_卫星失效不回退预报(client, monkeypatch):
    from app.services import layers

    layers._satellite_images.clear()

    async def unavailable(_http):
        raise UpstreamUnavailable("卫星帧暂不可用")

    monkeypatch.setattr(layers.himawari, "latest_time", unavailable)
    response = await client.get(
        "/v1/map/layers/cloud", params={"bbox": "119,29,122,33", "source": "satellite"}
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


async def test_默认云图仍限制宽视野(client):
    response = await client.get("/v1/map/layers/cloud", params={"bbox": "92,36,128,56"})
    assert response.status_code == 400
