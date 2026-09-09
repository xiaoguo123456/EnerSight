"""云图帧归档与回放评估。"""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.satellite import archive, himawari, replay
from app.services import satellite as svc
from tests.fixtures_satellite import FakeSky, utc

LAT, LON = 31.30, 120.62
BBOX = svc.station_bbox(LAT, LON)


@pytest.fixture(autouse=True)
def _archive_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "archive_dir", str(tmp_path / "archive"))
    himawari.clear_cache()


class TestArchive:
    async def test_归档后可读回同一帧(self, monkeypatch):
        t = utc(3, 0)
        FakeSky().add(t, [(LON + 0.5, LAT + 0.5, 0.3)]).install(monkeypatch)
        n = await archive.archive_frame(None, t, "visible", BBOX)
        assert n > 0
        assert await archive.archive_frame(None, t, "visible", BBOX) == 0  # 已存在不重写

        online = await himawari.fetch_mosaic(None, t, "visible", BBOX)
        offline = archive.load_mosaic(t, "visible", BBOX)
        assert offline is not None
        assert offline.rgb.shape == online.rgb.shape
        assert abs(int(offline.rgb.mean()) - int(online.rgb.mean())) <= 2  # JPEG 编码误差
        assert archive.archived_times("visible", t) == [t]
        assert archive.load_mosaic(t, "infrared", BBOX) is None

    async def test_按保留期清理(self, monkeypatch):
        old = datetime.now(UTC) - timedelta(days=70)
        recent = utc(3, 0)
        FakeSky().add(old).add(recent).install(monkeypatch)
        await archive.archive_frame(None, old, "infrared", BBOX)
        await archive.archive_frame(None, recent, "infrared", BBOX)
        assert archive.prune(60) == 1
        assert archive.load_mosaic(old, "infrared", BBOX) is None
        assert archive.load_mosaic(recent, "infrared", BBOX) is not None


class TestReplay:
    async def test_逼近云团被命中(self, monkeypatch):
        """云团从东北 1.1° 处每 10 分钟移近 0.08°，约 14 帧后覆盖站点"""
        t0 = utc(1, 0)
        sky = FakeSky()
        times = []
        for i in range(16):
            t = t0 + timedelta(minutes=10 * i)
            off = 1.1 - 0.08 * i
            sky.add(t, [(LON + off, LAT + off, 0.5)])
            times.append(t)
        sky.install(monkeypatch)
        for t in times:
            await archive.archive_frame(None, t, "visible", BBOX)

        st = replay.evaluate(times, "visible", BBOX, LAT, LON, svc.CLOUD_THRESHOLD["visible"])
        assert st.frames == 16
        assert st.predictions >= 5
        assert st.false_alarms == 0 and st.misses == 0
        assert st.hit_rate is not None and st.hit_rate >= 0.5
        assert st.mae_min is not None and st.mae_min <= replay.TOLERANCE_MIN + 5
        print(st)

    async def test_远离云团不出预测也不算漏报(self, monkeypatch):
        t0 = utc(1, 0)
        sky = FakeSky()
        times = []
        for i in range(6):
            t = t0 + timedelta(minutes=10 * i)
            sky.add(t, [(LON + 1.0 + 0.08 * i, LAT + 1.0 + 0.08 * i, 0.5)])
            times.append(t)
        sky.install(monkeypatch)
        for t in times:
            await archive.archive_frame(None, t, "visible", BBOX)
        st = replay.evaluate(times, "visible", BBOX, LAT, LON, svc.CLOUD_THRESHOLD["visible"])
        assert st.predictions == 0 and st.misses == 0 and st.false_alarms == 0


class TestBackfill:
    def test_回补最近若干帧(self):
        times = [utc(3, 0) + timedelta(minutes=10 * i) for i in range(10)]
        assert archive.backfill_frames(times, 3) == times[-3:]
        assert archive.backfill_frames(times, 0) == times[-1:]  # 至少一帧
        assert archive.backfill_frames(times[:2], 6) == times[:2]
        assert archive.backfill_frames([], 6) == []

    async def test_最新帧未就绪时上一帧仍被归档(self, monkeypatch):
        """JMA 的时刻列表先于瓦片更新：只归档 latest 会把这一帧永久漏掉"""
        prev, latest = utc(3, 0), utc(3, 10)
        FakeSky().add(prev, [(LON + 0.5, LAT + 0.5, 0.3)]).add(latest, missing=True).install(
            monkeypatch
        )
        times = await himawari.available_times(None)
        written = 0
        for when in archive.backfill_frames(times, 2):
            try:
                written += await archive.archive_frame(None, when, "visible", BBOX)
            except himawari.UpstreamUnavailable:
                continue
        assert written > 0
        assert archive.load_mosaic(prev, "visible", BBOX) is not None
        assert archive.load_mosaic(latest, "visible", BBOX) is None
