"""地图栅格空间正确性、覆盖、缺测及版本缓存回归。"""

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds

from app.geo import gcj02_to_wgs84
from app.geo.gcj02 import gcj02_to_wgs84_array
from app.render import map_raster as m
from app.render.colormap import SCALES


def test_全国视野固定瓦片全覆盖且限制数量():
    for bbox in [(70, 5, 140, 60), (80, 34, 94, 51), (120, 30, 120.1, 30.1)]:
        result = m.viewport_tiles(bbox)
        assert len(result) <= 12
        bounds = [m.xyz_bounds(*t) for t in result]
        assert min(b[0] for b in bounds) <= bbox[0]
        assert min(b[1] for b in bounds) <= bbox[1]
        assert max(b[2] for b in bounds) >= bbox[2]
        assert max(b[3] for b in bounds) >= bbox[3]
    assert m.viewport_tiles((70, 5, 140, 60))[0][0] < m.viewport_tiles((120, 30, 120.1, 30.1))[0][0]


def test_像素坐标变换与现有站点坐标一致():
    lon = np.array([87.6, 121.5, 116.4, 65.0])
    lat = np.array([43.8, 31.2, 39.9, 40.0])
    wl, wt = gcj02_to_wgs84_array(lon, lat)
    expected = np.array([gcj02_to_wgs84(x, y) for x, y in zip(lon, lat, strict=True)])
    assert np.allclose(wl, expected[:, 0], atol=1e-8)
    assert np.allclose(wt, expected[:, 1], atol=1e-8)


def test_COG概览及墨卡托行采样不是线性纬度拉伸(tmp_path):
    # 以纬度作为辐射合成量，方便核对任意图像行对应的真实位置。
    h, w = 900, 1200
    lat = 80 - (np.arange(h) + 0.5) * 80 / h
    field = np.broadcast_to(lat[:, None] * 10, (h, w)).astype("float32").copy()
    cog = tmp_path / "test.tif"
    m._write_cog(cog, field[None], from_bounds(0, 0, 160, 80, w, h))
    with rasterio.open(cog) as src:
        assert src.overviews(1)
        assert src.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
    out = tmp_path / "tile.png"
    m.render_tile(cog, out, "radiation", 2, 3, 1)
    image = np.asarray(Image.open(out))
    # z2/y1: 赤道到66.5N；墨卡托中间一行纬度约41度，不是33度。
    north = np.pi / 2
    lat_center = np.degrees(np.arctan(np.sinh(north * (1 - 128.5 / 256))))
    expected = SCALES["radiation"].rgba(np.array([[lat_center * 10]]))[0, 0]
    assert np.max(np.abs(image[128, 40].astype(int) - expected.astype(int))) <= 3
    first_mtime = out.stat().st_mtime_ns
    m.render_tile(cog, out, "radiation", 2, 3, 1)
    assert out.stat().st_mtime_ns == first_mtime


def test_缺测及覆盖以外透明不冒充零值(tmp_path):
    cog = tmp_path / "nan.tif"
    m._write_cog(
        cog, np.full((1, 64, 64), np.nan, dtype="float32"), from_bounds(60, 0, 150, 65, 64, 64)
    )
    out = tmp_path / "nan.png"
    m.render_tile(cog, out, "temperature", 3, 5, 2)
    assert not np.asarray(Image.open(out))[:, :, 3].any()
    assert m.intersect((-180, -80, 180, 80)) == m.COVERAGE
    assert m.intersect((-120, 0, -100, 20)) is None


def test_成果选择不拿未来值并限制陈旧时间(tmp_path):
    now = datetime(2026, 9, 10, 3, tzinfo=UTC)

    def put(run, valid):
        path = m.root(str(tmp_path)) / m.stamp(run) / m.stamp(valid)
        path.mkdir(parents=True, exist_ok=True)
        record = {"run_at": run.isoformat(), "valid_at": valid.isoformat(), "layer": "wind"}
        (path / "wind.json").write_text(json.dumps(record))
        for coord in ("wgs84", "gcj02"):
            (path / f"wind_{coord}.tif").touch()
        return path

    oldrun = now - timedelta(hours=9)
    put(oldrun, now)
    newrun = now - timedelta(hours=3)
    put(newrun, now + timedelta(hours=1))
    assert m.available(str(tmp_path), "wind", now)[1]["run_at"] == oldrun.isoformat()
    path = put(newrun, now)
    assert m.available(str(tmp_path), "wind", now)[1]["run_at"] == newrun.isoformat()
    (path / "wind_gcj02.tif").unlink()
    assert m.available(str(tmp_path), "wind", now)[1]["run_at"] == oldrun.isoformat()
    assert m.available(str(tmp_path), "wind", now + timedelta(hours=4)) is None


async def test_冷启动接口只报告准备中不在线下载(client, tmp_path, monkeypatch):
    from app.render import tiles

    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    response = await client.get("/v1/map/layers/temperature", params={"bbox": "70,5,140,60"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MAP_PREPARING"


def test_成果缺一个坐标文件时能够重新准备(tmp_path, monkeypatch):
    from app.render import hres, tiles

    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    meta = {"reference_time": now.isoformat()}
    calls = []

    def read(region, layer, meta, valid):
        calls.append(layer)
        return hres.Frame(
            region,
            meta["reference_time"],
            valid.isoformat(),
            {"temperature_2m": np.full((64, 64), 20, dtype="float32")},
            0,
        )

    monkeypatch.setattr(hres, "read_frame", read)
    path = m.prepare(str(tmp_path), "temperature", meta, now)
    from pathlib import Path

    Path(path).with_name("temperature_gcj02.tif").unlink()
    m.prepare(str(tmp_path), "temperature", meta, now)
    assert len(calls) == 2
    assert Path(path).with_name("temperature_gcj02.tif").exists()


def test_保留旧起报批次的当前有效时刻(tmp_path):
    now = datetime.now(UTC)
    run = m.root(str(tmp_path)) / m.stamp(now - timedelta(days=4))
    current = run / m.stamp(now)
    old = run / m.stamp(now - timedelta(days=3))
    current.mkdir(parents=True)
    old.mkdir()
    m.prune(str(tmp_path))
    assert current.exists() and not old.exists()
