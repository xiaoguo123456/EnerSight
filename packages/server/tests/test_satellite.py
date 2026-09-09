from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest
import respx
from httpx import AsyncClient, Response

from app.render import tiles
from app.satellite import himawari, motion
from app.satellite.himawari import lonlat_to_tile, tile_to_lonlat
from app.satellite.reproject import reproject
from app.services import satellite, weather
from tests.fixtures_forecast import TZ, make_forecast
from tests.fixtures_satellite import FakeSky, utc

SUZHOU = {
    "name": "苏州光伏站",
    "type": "solar",
    "latitude": 31.30,
    "longitude": 120.62,
    "capacity": 500,
}
LAT, LON = SUZHOU["latitude"], SUZHOU["longitude"]
BBOX = (118.0, 28.5, 123.0, 33.5)
DAY = utc(3, 0)  # 11:00 当地，太阳高度角够
NIGHT = utc(16, 0)  # 00:00 当地


def _yesterday_midnight() -> datetime:
    now = datetime.now(ZoneInfo(TZ))
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    weather.clear_cache()
    himawari.clear_cache()
    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    yield
    weather.clear_cache()


@pytest.fixture
def open_meteo():
    with respx.mock(assert_all_called=False) as mock:
        yield mock.get(url__regex=r".*open-meteo.*").mock(
            return_value=Response(200, json=make_forecast(start_date=_yesterday_midnight()))
        )


def _approaching(sky: FakeSky, now: datetime) -> FakeSky:
    """东北方约 1.1° 处的云团，10 分钟内向站点移动约 0.08°"""
    sky.add(now - timedelta(minutes=10), [(LON + 1.1, LAT + 1.1, 0.5)])
    sky.add(now, [(LON + 1.02, LAT + 1.02, 0.5)])
    return sky


class TestTiles:
    def test_瓦片坐标往返(self):
        for lon, lat in ((120.62, 31.30), (-73.9, 40.7), (0.0, 0.0)):
            x, y = lonlat_to_tile(lon, lat, 5)
            lon2, lat2 = tile_to_lonlat(x, y, 5)
            assert abs(lon2 - lon) < 1e-9 and abs(lat2 - lat) < 1e-9

    def test_苏州所在瓦片(self):
        x, y = lonlat_to_tile(LON, LAT, 5)
        assert (int(x), int(y)) == (26, 13)

    def test_昼夜按太阳高度角(self):
        assert satellite.is_day(LAT, LON, DAY)
        assert not satellite.is_day(LAT, LON, NIGHT)


class TestReproject:
    async def test_站点落在对应像素(self, monkeypatch):
        FakeSky().add(DAY, [(LON, LAT, 0.08)]).install(monkeypatch)
        mosaic = await himawari.fetch_mosaic(None, DAY, "visible", BBOX)
        rep = reproject(mosaic, BBOX, 256)
        sx = int((LON - BBOX[0]) / 5 * 256)
        sy = int((BBOX[3] - LAT) / 5 * 256)
        assert rep.gray[sy, sx] > 150
        assert rep.gray[10, 10] < 60
        assert rep.rgb.shape == (256, 256, 3)

    async def test_最新帧瓦片未就绪退回上一帧(self, monkeypatch):
        sky = FakeSky().add(DAY - timedelta(minutes=10)).add(DAY, missing=True).install(monkeypatch)
        mosaic = await himawari.fetch_latest_mosaic(None, "visible", BBOX)
        assert mosaic.observed_at == DAY - timedelta(minutes=10)
        assert [t for t, _ in sky.calls][0] == DAY  # 先试了最新帧


class TestMotion:
    @staticmethod
    def _frame(cx, cy, size=512, r=60):
        from scipy.ndimage import shift as nd_shift

        rng = np.random.default_rng(0)
        yy, xx = np.mgrid[0:size, 0:size]
        tex = np.clip(
            0.75 + 0.25 * np.sin(xx / 6.0) * np.cos(yy / 7.0) + rng.random((size, size)) * 0.1, 0, 1
        )
        blob = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r))
        t = nd_shift(tex, (cy - size / 2, cx - size / 2), order=1, mode="wrap")
        return (30 + np.clip(blob * t, 0, 1) * 200).astype(np.uint8)

    def _station_px(self):
        return (LON - BBOX[0]) / 5 * 512, (BBOX[3] - LAT) / 5 * 512

    def test_东北方向云团向西南逼近(self):
        sx, sy = self._station_px()
        est = motion.estimate(
            self._frame(sx + 110, sy - 110), self._frame(sx + 102, sy - 102), BBOX, LAT, LON
        )
        assert est is not None
        assert est.heading_text == "西南" and est.origin_text == "东北"
        # 8px 对角 ≈ 11.4 km / 10 min ≈ 68 km/h，光流略低估
        assert 50 < est.speed_kmh < 80
        assert est.distance_km is not None and 40 < est.distance_km < 100
        assert est.impact_minutes is not None and 30 < est.impact_minutes <= 120

    def test_帧间隔20分钟速度减半(self):
        sx, sy = self._station_px()
        a, b = self._frame(sx + 110, sy - 110), self._frame(sx + 102, sy - 102)
        v10 = motion.estimate(a, b, BBOX, LAT, LON).speed_kmh
        v20 = motion.estimate(a, b, BBOX, LAT, LON, frame_minutes=20).speed_kmh
        assert abs(v20 * 2 - v10) < 1e-6

    def test_远离不出影响(self):
        sx, sy = self._station_px()
        est = motion.estimate(
            self._frame(sx + 110, sy - 110), self._frame(sx + 118, sy - 118), BBOX, LAT, LON
        )
        assert est is not None and est.heading_text == "东北" and est.impact_minutes is None

    def test_侧向掠过不出影响(self):
        """向东南移动，方向偏离站点超过 45°"""
        sx, sy = self._station_px()
        est = motion.estimate(
            self._frame(sx + 110, sy - 110), self._frame(sx + 118, sy - 102), BBOX, LAT, LON
        )
        assert est is not None and est.impact_minutes is None

    def test_站点已在云下(self):
        sx, sy = self._station_px()
        est = motion.estimate(self._frame(sx + 8, sy - 8), self._frame(sx, sy), BBOX, LAT, LON)
        assert est is not None and est.covered and est.impact_minutes == 0

    def test_无云返回None(self):
        blank = np.full((512, 512), 30, dtype=np.uint8)
        assert motion.estimate(blank, blank, BBOX, LAT, LON) is None

    def test_红外阈值更低(self):
        """红外亮温图整体偏暗，同一张图按可见光阈值会漏掉云"""
        sx, sy = self._station_px()
        dim = (self._frame(sx, sy).astype(float) * 0.45).astype(np.uint8)  # 峰值约 100
        assert motion.estimate(dim, dim, BBOX, LAT, LON) is None
        est = motion.estimate(
            dim, dim, BBOX, LAT, LON, cloud_threshold=satellite.CLOUD_THRESHOLD["infrared"]
        )
        assert est is not None and est.covered

    def test_16方位(self):
        degrees = (0, 45, 90, 135, 180, 225, 270, 315, 22.5, 359)
        expected = ["北", "东北", "东", "东南", "南", "西南", "西", "西北", "北北东", "北"]
        assert [motion.bearing_text(d) for d in degrees] == expected


class TestApi:
    async def _create(self, client: AsyncClient) -> str:
        res = await client.post("/v1/stations", json=SUZHOU)
        assert res.status_code == 201, res.text
        return res.json()["data"]["id"]

    async def test_白天返回真彩可见光(self, client: AsyncClient, open_meteo, monkeypatch, tmp_path):
        sid = await self._create(client)
        sky = FakeSky().add(DAY, [(LON + 1.0, LAT + 1.0, 0.4)]).install(monkeypatch)
        res = await client.get(f"/v1/satellite/cloud?station_id={sid}&coord=gcj02")
        assert res.status_code == 200, res.text
        d = res.json()["data"]
        assert d["band"] == "visible"
        assert d["observed_at"].endswith("+08:00")
        assert d["image"]["url"].startswith("http://test/tiles/satellite/")
        assert d["image"]["url"].endswith("_truecolor.png")
        assert d["legend"]["labels"] == ["低", "高"]
        # bounds 与站点标记均已转 GCJ-02（偏移几百米），站点仍在 bounds 内
        b = d["image"]["bounds"]
        m = d["station_marker"]
        assert b["sw"]["latitude"] < m["latitude"] < b["ne"]["latitude"]
        assert abs(m["longitude"] - LON) > 0.001
        png = tmp_path / "tiles" / d["image"]["url"].split("/tiles/")[1]
        assert png.exists() and png.stat().st_size > 1000
        assert {band for _, band in sky.calls} == {"visible", "truecolor"}

    async def test_夜间返回红外(self, client: AsyncClient, open_meteo, monkeypatch):
        sid = await self._create(client)
        sky = FakeSky().add(NIGHT, [(LON + 1.0, LAT + 1.0, 0.4)], night=True).install(monkeypatch)
        res = await client.get(f"/v1/satellite/cloud?station_id={sid}")
        assert res.status_code == 200, res.text
        d = res.json()["data"]
        assert d["band"] == "infrared"
        assert d["image"]["url"].endswith("_infrared.png")
        assert {band for _, band in sky.calls} == {"infrared"}

    async def test_上游故障502(self, client: AsyncClient, open_meteo):
        sid = await self._create(client)
        res = await client.get(f"/v1/satellite/cloud?station_id={sid}")
        assert res.status_code == 502

    async def test_当前预警带云图与外推(self, client: AsyncClient, open_meteo, monkeypatch):
        sid = await self._create(client)
        _approaching(FakeSky(), DAY).install(monkeypatch)

        res = await client.get(f"/v1/alerts/current?station_id={sid}")
        assert res.status_code == 200, res.text
        d = res.json()["data"]
        assert d["satellite_status"] == "ok"
        assert d["satellite"] is not None and d["satellite"]["band"] == "visible"
        assert d["alert"] is not None and d["alert"]["source"] == "satellite"
        assert "云团逼近" in d["alert"]["title"]
        cm = d["cloud_motion"]
        assert cm is not None
        assert cm["direction"] == "西南"
        assert "东北" in cm["direction_detail"]
        assert 0 < cm["impact_in_minutes"] <= 120
        assert cm["reference_station"] == "距苏州光伏站"
        assert cm["distance_km"] > 0

        # 列表里也能看到这条卫星预警
        lst = await client.get(f"/v1/alerts?station_id={sid}")
        assert any(a["source"] == "satellite" for a in lst.json()["data"]["alerts"])

    async def test_夜间红外也能出短临预警(self, client: AsyncClient, open_meteo, monkeypatch):
        sid = await self._create(client)
        sky = _approaching(FakeSky(), NIGHT)
        sky.night_times.update(sky.frames)
        sky.install(monkeypatch)
        d = (await client.get(f"/v1/alerts/current?station_id={sid}")).json()["data"]
        assert d["satellite"]["band"] == "infrared"
        assert d["alert"] is not None and d["alert"]["source"] == "satellite"
        assert d["cloud_motion"] is not None

    async def test_上一帧间隔过大不做外推(self, client: AsyncClient, open_meteo, monkeypatch):
        sid = await self._create(client)
        sky = FakeSky()
        sky.add(DAY - timedelta(minutes=40), [(LON + 1.1, LAT + 1.1, 0.5)])
        sky.add(DAY, [(LON + 1.02, LAT + 1.02, 0.5)])
        sky.install(monkeypatch)
        d = (await client.get(f"/v1/alerts/current?station_id={sid}")).json()["data"]
        assert d["satellite"] is not None
        assert d["cloud_motion"] is None

    async def test_卫星不可用时预报预警照常(self, client: AsyncClient, open_meteo):
        sid = await self._create(client)
        res = await client.get(f"/v1/alerts/current?station_id={sid}")
        assert res.status_code == 200
        d = res.json()["data"]
        assert d["satellite"] is None and d["cloud_motion"] is None
        assert d["satellite_status"] == "unavailable"

    async def test_卫星断供不清除卫星预警_确认无云才清(
        self, client: AsyncClient, open_meteo, monkeypatch
    ):
        """未知 ≠ 消失：上游故障时保留卫星预警；拿到云图确认云已散才解除"""
        sid = await self._create(client)
        _approaching(FakeSky(), DAY).install(monkeypatch)
        d = (await client.get(f"/v1/alerts/current?station_id={sid}")).json()["data"]
        assert d["alert"]["source"] == "satellite" and d["satellite_status"] == "ok"

        FakeSky().install(monkeypatch)  # 没有任何可用时刻 → 上游故障
        d = (await client.get(f"/v1/alerts/current?station_id={sid}")).json()["data"]
        assert d["satellite_status"] == "unavailable"
        assert d["alert"] is not None and d["alert"]["source"] == "satellite"
        assert d["cloud_motion"] is None

        FakeSky().add(DAY + timedelta(minutes=10)).install(monkeypatch)  # 晴空
        d = (await client.get(f"/v1/alerts/current?station_id={sid}")).json()["data"]
        assert d["satellite_status"] == "ok"
        assert d["alert"] is None
        lst = (await client.get(f"/v1/alerts?station_id={sid}")).json()["data"]["alerts"]
        assert lst[0]["level"] == "cleared" and lst[0]["source"] == "satellite"


class TestMapCloudLayer:
    async def test_白天云图层用可见光(self, client: AsyncClient, monkeypatch):
        FakeSky().add(DAY, [(LON, LAT, 0.5)]).install(monkeypatch)
        res = await client.get("/v1/map/layers/cloud?bbox=120,30,121,31&zoom=8")
        assert res.status_code == 200, res.text
        d = res.json()["data"]
        url = d["frames"][0]["images"][0]["url"]
        assert "/tiles/cloud-sat/" in url and url.endswith("_visible.png")
        assert d["observed_at"].startswith(DAY.strftime("%Y-%m-%dT03:00"))

    async def test_夜间云图层用红外(self, client: AsyncClient, monkeypatch):
        FakeSky().add(NIGHT, [(LON, LAT, 0.5)], night=True).install(monkeypatch)
        res = await client.get("/v1/map/layers/cloud?bbox=120,30,121,31&zoom=8")
        assert res.status_code == 200, res.text
        assert res.json()["data"]["frames"][0]["images"][0]["url"].endswith("_infrared.png")

    async def test_上游故障退回预报云量(self, client: AsyncClient):
        from app.render import grid
        from tests.test_layers import _grid_response

        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=r".*open-meteo.*").mock(
                return_value=Response(200, json=_grid_response(grid.N * grid.N))
            )
            res = await client.get("/v1/map/layers/cloud?bbox=120,30,121,31&zoom=8")
        assert res.status_code == 200, res.text
        assert "/tiles/cloud/" in res.json()["data"]["frames"][0]["images"][0]["url"]


async def test_历史时轴仅包含近三小时真实帧(client, monkeypatch):
    sky = FakeSky().install(monkeypatch)
    for minutes in [0, 10, 30, 180, 190]:
        sky.add(DAY - timedelta(minutes=minutes))
    await client.post("/v1/stations", json=SUZHOU)
    result = await client.get("/v1/satellite/cloud/history")
    assert result.status_code == 200
    times = result.json()["data"]["times"]
    assert len(times) == 4
    assert times == sorted(times)
    assert (DAY - timedelta(minutes=20)).isoformat() not in times


async def test_历史云图使用真彩色且不伪造缺帧(client, monkeypatch):
    from app.errors import ApiError
    from app.models import Station
    from app.schemas.common import Coord

    sky = FakeSky().add(DAY, [(LON, LAT, 0.5)]).install(monkeypatch)
    satellite._history_cache.clear()
    station = Station(
        id="history-test", name="历史云图测试", latitude=LAT, longitude=LON, type="solar"
    )
    async with AsyncClient() as http:
        data = await satellite.cloud_at(http, station, DAY, Coord.WGS84, "http://test")
        assert data.band == "visible"
        assert data.observed_at == DAY.isoformat()
        assert all(band == "truecolor" for _, band in sky.calls)
        assert (
            (tiles.tile_dir() / data.image.url.split("/tiles/")[1])
            .read_bytes()
            .startswith(b"\x89PNG")
        )
        with pytest.raises(ApiError):
            await satellite.cloud_at(
                http, station, DAY - timedelta(minutes=10), Coord.WGS84, "http://test"
            )


async def test_历史时轴排除夜间帧(client, monkeypatch):
    sky = FakeSky().add(NIGHT).install(monkeypatch)
    await client.post("/v1/stations", json=SUZHOU)
    result = await client.get("/v1/satellite/cloud/history")
    assert result.status_code == 200
    assert result.json()["data"]["times"] == []
    assert sky.calls == []
