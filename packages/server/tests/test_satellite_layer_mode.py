"""首页卫星入口必须拿真实、同一时刻的卫星帧。"""

import os
from datetime import UTC, datetime, timedelta
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

    async def mosaic(_http, band, bbox, zoom=None):
        calls.append((band, bbox))
        assert zoom == 4
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
    assert data["frames"][0]["images"][0]["url"].endswith(".jpg")
    image = next((tmp_path / "tiles" / "cloud-image").rglob("*.jpg"))
    assert image.read_bytes().startswith(b"\xff\xd8")
    assert calls[0][0] == "truecolor"


async def test_省域时间轴只列真实帧且历史帧按指定时刻取(client, monkeypatch, tmp_path):
    from app.render import tiles
    from app.services import layers

    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    layers._satellite_images.clear()
    end = datetime(2026, 9, 23, 4, 20, tzinfo=UTC)
    available = [end - timedelta(minutes=10 * i) for i in reversed(range(20))]
    requested = []

    async def times(_http):
        return available

    async def mosaic(_http, when, band, bbox, zoom=None):
        requested.append((when, band, bbox))
        assert zoom == 4
        return SimpleNamespace(observed_at=when)

    def project(_mosaic, bbox, _size):
        rgb = np.full((4, 4, 3), 120, dtype=np.uint8)
        return Reprojected(rgb=rgb, gray=rgb[..., 0], bbox=bbox)

    monkeypatch.setattr(layers.himawari, "available_times", times)
    monkeypatch.setattr(layers.himawari, "fetch_mosaic", mosaic)
    monkeypatch.setattr(layers.satellite, "analysis_band", lambda *_: "visible")
    monkeypatch.setattr(layers, "reproject", project)

    history = await client.get("/v1/map/layers/cloud/history")
    assert history.status_code == 200
    sampled = history.json()["data"]["times"]
    assert len(sampled) == 10
    assert sampled[0] in [t.isoformat(timespec="minutes") for t in available]
    assert sampled[-1] == end.isoformat(timespec="minutes")

    frame = await client.get("/v1/map/layers/cloud", params={
        "bbox": "92,36,128,56", "source": "satellite", "at": sampled[0],
    })
    assert frame.status_code == 200, frame.text
    assert frame.json()["data"]["observed_at"] == sampled[0]
    assert requested[0][0].isoformat(timespec="minutes") == sampled[0]
    layers._satellite_images.clear()
    replay = await client.get("/v1/map/layers/cloud", params={
        "bbox": "92,36,128,56", "source": "satellite", "at": sampled[0],
    })
    assert replay.status_code == 200
    assert len(requested) == 1  # 已落盘的帧直接返回，不再拼接瓦片

    invalid = await client.get("/v1/map/layers/cloud", params={
        "bbox": "92,36,128,56", "source": "satellite", "at": (end - timedelta(hours=4)).isoformat(),
    })
    assert invalid.status_code == 400


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


def test_省域历史帧到期自动清理且不影响其他图层(monkeypatch, tmp_path):
    from app.render import tiles

    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    old = tmp_path / "tiles" / "cloud-image" / "province" / "old.jpg"
    recent = old.with_name("recent.jpg")
    other = tmp_path / "tiles" / "cloud-sat" / "province" / "old.png"
    for path in (old, recent, other):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")
    now = 1_800_000_000.0
    for path in (old, other):
        os.utime(path, (now - 49 * 3600, now - 49 * 3600))
    os.utime(recent, (now - 47 * 3600, now - 47 * 3600))

    assert tiles.prune_cloud_images(now=now) == 1
    assert not old.exists()
    assert recent.exists() and other.exists()
