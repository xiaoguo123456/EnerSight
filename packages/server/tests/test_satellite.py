from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest
import respx
from httpx import AsyncClient, Response

from app.render import tiles
from app.satellite import himawari, motion
from app.satellite.reproject import is_daylit, reproject
from app.services import weather
from tests.fixtures_forecast import TZ, make_forecast
from tests.fixtures_satellite import disk_px, make_disk, utc

SUZHOU = {
    "name": "苏州光伏站",
    "type": "solar",
    "latitude": 31.30,
    "longitude": 120.62,
    "capacity": 500,
}
LAT, LON = SUZHOU["latitude"], SUZHOU["longitude"]
BBOX = (118.0, 28.5, 123.0, 33.5)


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    weather.clear_cache()
    himawari.clear_cache()
    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    yield
    weather.clear_cache()


def _yesterday_midnight() -> datetime:
    now = datetime.now(ZoneInfo(TZ))
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


@pytest.fixture
def open_meteo():
    with respx.mock(assert_all_called=False) as mock:
        yield mock.get(url__regex=r".*open-meteo.*").mock(
            return_value=Response(200, json=make_forecast(start_date=_yesterday_midnight()))
        )


def _install_disks(monkeypatch, now, prev=None):
    """替换全圆盘拉取：按时间返回合成帧"""
    frames = {now.observed_at: now}
    if prev is not None:
        frames[prev.observed_at] = prev

    async def _fetch(_http, when=None):
        when = when or now.observed_at
        if when not in frames:
            raise himawari.UpstreamUnavailable("no frame")
        return frames[when]

    monkeypatch.setattr(himawari, "fetch_full_disk", _fetch)


class TestReproject:
    def test_站点落在圆盘对应像素(self):
        """圆盘上站点位置涂白，重投影后站点像素应该是亮的，远处是暗的"""
        disk = make_disk(utc(3, 0), [(LON, LAT, 0.08)])
        rep = reproject(disk.rgb, BBOX, 256)
        sx = int((LON - BBOX[0]) / 5 * 256)
        sy = int((BBOX[3] - LAT) / 5 * 256)
        assert rep.gray[sy, sx] > 150
        assert rep.gray[10, 10] < 60
        assert rep.rgb.shape == (256, 256, 3)

    def test_圆盘像素与已验证坐标一致(self):
        # 上海 → (753, 467)，海岸线人工核对过的值，投影参数改错这里先炸
        x, y = disk_px(121.47, 31.23)
        assert (round(x), round(y)) == (753, 467)

    def test_夜间判定(self):
        assert not is_daylit(np.zeros((8, 8), dtype=np.uint8))
        assert is_daylit(np.full((8, 8), 60, dtype=np.uint8))


class TestFetch:
    async def test_最新帧不全时退回上一帧(self, monkeypatch):
        """latest.json 可能先于瓦片更新；缺瓦片不能拿黑图当夜间"""
        latest = utc(3, 0)
        calls: list[str] = []

        async def _latest(_http):
            return latest

        async def _at(_http, when):
            calls.append(when.strftime("%H%M"))
            if when == latest:
                raise himawari.UpstreamUnavailable("not ready")
            return make_disk(when)

        monkeypatch.setattr(himawari, "latest_time", _latest)
        monkeypatch.setattr(himawari, "_fetch_at", _at)
        disk = await himawari.fetch_full_disk(None)
        assert calls == ["0300", "0250"]
        assert disk.observed_at == latest - timedelta(minutes=10)


class TestMotion:
    @staticmethod
    def _frame(cx, cy, size=512, r=60):
        rng = np.random.default_rng(0)
        yy, xx = np.mgrid[0:size, 0:size]
        tex = np.clip(
            0.75 + 0.25 * np.sin(xx / 6.0) * np.cos(yy / 7.0) + rng.random((size, size)) * 0.1, 0, 1
        )
        from scipy.ndimage import shift as nd_shift

        blob = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r))
        t = nd_shift(tex, (cy - size / 2, cx - size / 2), order=1, mode="wrap")
        return (30 + np.clip(blob * t, 0, 1) * 200).astype(np.uint8)

    def _station_px(self):
        return (LON - BBOX[0]) / 5 * 512, (BBOX[3] - LAT) / 5 * 512

    def test_东北方向云团向西南逼近(self):
        sx, sy = self._station_px()
        prev = self._frame(sx + 110, sy - 110)
        now = self._frame(sx + 102, sy - 102)
        est = motion.estimate(prev, now, BBOX, LAT, LON)
        assert est is not None
        assert est.heading_text == "西南" and est.origin_text == "东北"
        # 8px 对角 ≈ 11.4 km / 10 min ≈ 68 km/h，光流略低估
        assert 50 < est.speed_kmh < 80
        assert est.distance_km is not None and 40 < est.distance_km < 100
        assert est.impact_minutes is not None and 30 < est.impact_minutes <= 120

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

    def test_16方位(self):
        degrees = (0, 45, 90, 135, 180, 225, 270, 315, 22.5, 359)
        expected = ["北", "东北", "东", "东南", "南", "西南", "西", "西北", "北北东", "北"]
        assert [motion.bearing_text(d) for d in degrees] == expected


class TestApi:
    async def _create(self, client: AsyncClient) -> str:
        res = await client.post("/v1/stations", json=SUZHOU)
        assert res.status_code == 201, res.text
        return res.json()["data"]["id"]

    async def test_白天返回云图(self, client: AsyncClient, open_meteo, monkeypatch, tmp_path):
        sid = await self._create(client)
        _install_disks(monkeypatch, make_disk(utc(3, 0), [(LON + 1.0, LAT + 1.0, 0.4)]))
        res = await client.get(f"/v1/satellite/cloud?station_id={sid}&coord=gcj02")
        assert res.status_code == 200, res.text
        d = res.json()["data"]
        assert d["band"] == "visible"
        assert d["observed_at"].endswith("+08:00")
        assert d["image"]["url"].startswith("http://test/tiles/satellite/")
        assert d["legend"]["labels"] == ["低", "高"]
        # bounds 与站点标记均已转 GCJ-02（偏移几百米），站点仍在 bounds 内
        b = d["image"]["bounds"]
        m = d["station_marker"]
        assert b["sw"]["latitude"] < m["latitude"] < b["ne"]["latitude"]
        assert abs(m["longitude"] - LON) > 0.001
        png = tmp_path / "tiles" / d["image"]["url"].split("/tiles/")[1]
        assert png.exists() and png.stat().st_size > 1000

    async def test_夜间503(self, client: AsyncClient, open_meteo, monkeypatch):
        sid = await self._create(client)
        _install_disks(monkeypatch, make_disk(utc(15, 0), night=True))
        res = await client.get(f"/v1/satellite/cloud?station_id={sid}")
        assert res.status_code == 503
        assert res.json()["error"]["code"] == "DATA_UNAVAILABLE"

    async def test_上游故障502(self, client: AsyncClient, open_meteo):
        sid = await self._create(client)
        res = await client.get(f"/v1/satellite/cloud?station_id={sid}")
        assert res.status_code == 502

    async def test_当前预警带云图与外推(self, client: AsyncClient, open_meteo, monkeypatch):
        sid = await self._create(client)
        now_t = utc(3, 0)
        # 东北方约 1.1° 处的云团，10 分钟内向站点移动约 0.08°
        prev = make_disk(now_t - timedelta(minutes=10), [(LON + 1.1, LAT + 1.1, 0.5)])
        now = make_disk(now_t, [(LON + 1.02, LAT + 1.02, 0.5)])
        _install_disks(monkeypatch, now, prev)

        res = await client.get(f"/v1/alerts/current?station_id={sid}")
        assert res.status_code == 200, res.text
        d = res.json()["data"]
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

    async def test_卫星不可用时预报预警照常(self, client: AsyncClient, open_meteo):
        sid = await self._create(client)
        res = await client.get(f"/v1/alerts/current?station_id={sid}")
        assert res.status_code == 200
        d = res.json()["data"]
        assert d["satellite"] is None and d["cloud_motion"] is None
        assert d["satellite_status"] == "unavailable"

    async def test_卫星断供不清除卫星预警_夜间才清(
        self, client: AsyncClient, open_meteo, monkeypatch
    ):
        """未知 ≠ 消失：上游故障时保留卫星预警；确定是夜间（无云图）才解除"""
        sid = await self._create(client)
        now_t = utc(3, 0)
        prev = make_disk(now_t - timedelta(minutes=10), [(LON + 1.1, LAT + 1.1, 0.5)])
        now = make_disk(now_t, [(LON + 1.02, LAT + 1.02, 0.5)])
        _install_disks(monkeypatch, now, prev)
        d = (await client.get(f"/v1/alerts/current?station_id={sid}")).json()["data"]
        assert d["alert"]["source"] == "satellite" and d["satellite_status"] == "ok"

        # 上游故障：预警保留，只是没有云图与外推
        async def _boom(*_a, **_k):
            raise himawari.UpstreamUnavailable("down")

        monkeypatch.setattr(himawari, "fetch_full_disk", _boom)
        d = (await client.get(f"/v1/alerts/current?station_id={sid}")).json()["data"]
        assert d["satellite_status"] == "unavailable"
        assert d["alert"] is not None and d["alert"]["source"] == "satellite"
        assert d["cloud_motion"] is None

        # 夜间：确定无云图，解除
        _install_disks(monkeypatch, make_disk(utc(15, 0), night=True))
        d = (await client.get(f"/v1/alerts/current?station_id={sid}")).json()["data"]
        assert d["satellite_status"] == "night"
        assert d["alert"] is None
        lst = (await client.get(f"/v1/alerts?station_id={sid}")).json()["data"]["alerts"]
        assert lst[0]["level"] == "cleared" and lst[0]["source"] == "satellite"


class TestMapCloudLayer:
    async def test_白天云图层用卫星(self, client: AsyncClient, monkeypatch, tmp_path):
        _install_disks(monkeypatch, make_disk(utc(3, 0), [(LON, LAT, 0.5)]))
        res = await client.get("/v1/map/layers/cloud?bbox=120,30,121,31&zoom=8")
        assert res.status_code == 200, res.text
        d = res.json()["data"]
        assert "/tiles/cloud-sat/" in d["frames"][0]["images"][0]["url"]
        assert d["observed_at"].startswith(utc(3, 0).strftime("%Y-%m-%dT03:00"))

    async def test_夜间退回预报云量(self, client: AsyncClient, monkeypatch):
        from app.render import grid
        from tests.test_layers import _grid_response

        _install_disks(monkeypatch, make_disk(utc(15, 0), night=True))
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=r".*open-meteo.*").mock(
                return_value=Response(200, json=_grid_response(grid.N * grid.N))
            )
            res = await client.get("/v1/map/layers/cloud?bbox=120,30,121,31&zoom=8")
        assert res.status_code == 200, res.text
        assert "/tiles/cloud/" in res.json()["data"]["frames"][0]["images"][0]["url"]
