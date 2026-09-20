"""卫星辐照实况。docs/19 §四"""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from httpx import AsyncClient

from app.config import settings
from app.models import Station
from app.services import satellite_irradiance as svc

pytestmark = pytest.mark.anyio

LAT, LON, TZ = 31.3, 120.62, "Asia/Shanghai"


def _station() -> Station:
    return Station(id="sat-ghi", name="卫星辐照测试", latitude=LAT, longitude=LON, type="solar")


def _payload(rows: list[tuple[datetime, float | None, float | None, float | None]]) -> dict:
    return {
        "hourly": {
            "time": [t.strftime("%Y-%m-%dT%H:%M") for t, *_ in rows],
            "shortwave_radiation": [g for _, g, _, _ in rows],
            "direct_radiation": [d for _, _, d, _ in rows],
            "diffuse_radiation": [f for _, _, _, f in rows],
        }
    }


def _install(monkeypatch, *, day: bool = True, payload=None, boom: Exception | None = None):
    monkeypatch.setattr(svc, "is_day", lambda *_: day)

    async def fake(_http, _station):
        if boom is not None:
            raise boom
        return payload

    monkeypatch.setattr(svc, "_fetch", fake)
    svc._cache._cache.clear()


class TestLatest:
    def test_取最后一个有值的格(self):
        now = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)
        raw = _payload(
            [
                (now - timedelta(minutes=20), 240.0, 30.0, 210.0),
                (now - timedelta(minutes=10), 264.0, 36.4, 227.6),
                (now, None, None, None),  # 最新一格还没到，不能当成 0
            ]
        )
        latest = svc._latest(raw)
        assert latest is not None
        when, ghi, direct, diffuse = latest
        assert when == now - timedelta(minutes=10)
        assert (ghi, direct, diffuse) == (264.0, 36.4, 227.6)

    def test_全缺测返回空(self):
        now = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)
        assert svc._latest(_payload([(now, None, None, None)])) is None
        assert svc._latest({}) is None


class TestGet:
    async def test_白天有值时给出晴空指数(self, monkeypatch):
        now = datetime.now(UTC)
        observed = now.replace(second=0, microsecond=0) - timedelta(minutes=10)
        _install(monkeypatch, payload=_payload([(observed, 264.0, 36.4, 227.6)]))
        async with AsyncClient() as http:
            out = await svc.get(http, _station(), TZ)
        assert out is not None
        assert out.status == "ok"
        assert out.ghi_w_m2 == 264.0
        assert out.direct_w_m2 == 36.4
        assert out.source == svc.SOURCE
        assert out.observed_at is not None and out.observed_at.endswith("+08:00")
        # 晴空基准只能是正数，指数是实况与它的比
        assert out.clear_sky_ghi_w_m2 is not None and out.clear_sky_ghi_w_m2 > 0
        assert out.clear_sky_index == pytest.approx(264.0 / out.clear_sky_ghi_w_m2, abs=0.002)

    async def test_夜间不是故障(self, monkeypatch):
        _install(monkeypatch, day=False, payload=_payload([]))
        async with AsyncClient() as http:
            out = await svc.get(http, _station(), TZ)
        assert out is not None
        assert out.status == "night"
        assert out.ghi_w_m2 is None and out.observed_at is None

    async def test_最新一帧过旧判过期(self, monkeypatch):
        stale = datetime.now(UTC) - timedelta(
            minutes=settings.satellite_irradiance_stale_minutes + 10
        )
        _install(monkeypatch, payload=_payload([(stale, 500.0, 300.0, 200.0)]))
        async with AsyncClient() as http:
            out = await svc.get(http, _station(), TZ)
        assert out is not None
        assert out.status == "stale"
        assert out.ghi_w_m2 is None

    async def test_上游失败只降级不抛(self, monkeypatch):
        _install(monkeypatch, boom=RuntimeError("upstream down"))
        async with AsyncClient() as http:
            out = await svc.get(http, _station(), TZ)
        assert out is not None
        assert out.status == "unavailable"
        assert out.ghi_w_m2 is None

    async def test_关掉开关就不查(self, monkeypatch):
        _install(monkeypatch, boom=RuntimeError("不该被调用"))
        monkeypatch.setattr(settings, "satellite_irradiance_enabled", False)
        async with AsyncClient() as http:
            assert await svc.get(http, _station(), TZ) is None


class TestRequest:
    async def test_请求参数与不退回官方(self, monkeypatch):
        from app.providers import open_meteo

        seen: dict = {}

        class FakeRes:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"hourly": {}}

            @staticmethod
            def raise_for_status() -> None:
                return None

        async def fake_get(_client, url, **kwargs):
            seen["url"] = url
            seen.update(kwargs)
            return FakeRes()

        monkeypatch.setattr(open_meteo, "weather_get", fake_get)
        monkeypatch.setattr(settings, "open_meteo_satellite_base", "http://self-hosted/v1")
        async with AsyncClient() as http:
            await open_meteo.OpenMeteoProvider(http).satellite_radiation(
                LAT, LON, start=datetime.now(UTC).date(), end=datetime.now(UTC).date()
            )
        assert seen["url"] == "http://self-hosted/v1/archive"
        params = seen["params"]
        # 不带 native 只会回小时均值，实况要等整点过完
        assert params["temporal_resolution"] == "native"
        assert params["models"] == settings.satellite_irradiance_model
        assert params["hourly"] == "shortwave_radiation,direct_radiation,diffuse_radiation"
        # 葵花只在自建实例里，退回官方只会拿到 400
        assert seen["allow_fallback"] is False


class TestSeries:
    """10 分钟的卫星值对齐到趋势的 15 分钟轴。docs/19 §四"""

    @staticmethod
    def _index(step: int, n: int) -> pd.DatetimeIndex:
        start = pd.Timestamp("2026-09-20 08:00", tz=TZ)
        return pd.DatetimeIndex([start + pd.Timedelta(minutes=step * i) for i in range(n)])

    def test_两个十分钟格按覆盖比例合成一个十五分钟格(self):
        # 08:00–08:15 由 08:00（覆盖 07:50–08:00 的后半）与 08:10 两格拼出：
        # 子区间 08:05、08:10 来自 08:10 那格，08:15 还没有 → 第一个点应为 null
        base = pd.Timestamp("2026-09-20 08:00", tz=TZ)
        rows = [
            (base, 100.0),
            (base + pd.Timedelta(minutes=10), 200.0),
            (base + pd.Timedelta(minutes=20), 260.0),
            (base + pd.Timedelta(minutes=30), 300.0),
        ]
        raw = {
            "hourly": {
                "time": [t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M") for t, _ in rows],
                "shortwave_radiation": [v for _, v in rows],
            }
        }
        index = self._index(15, 3)  # 08:00 / 08:15 / 08:30
        out = svc._resample(raw, index, 15)
        # 08:15 覆盖 (08:00, 08:15]：08:05、08:10 取 200，08:15 取 260 → (200+200+260)/3
        assert out[1] == pytest.approx(220.0, abs=0.05)
        # 08:30 覆盖 (08:15, 08:30]：08:20 取 260，08:25、08:30 取 300 → (260+300+300)/3
        assert out[2] == pytest.approx(286.7, abs=0.05)
        # 08:00 要 07:50、07:55 两个子区间，上游没有 → 宁可断线
        assert out[0] is None

    def test_缺测与未来时段给_null(self):
        base = pd.Timestamp("2026-09-20 08:00", tz=TZ)
        raw = {
            "hourly": {
                "time": [
                    (base + pd.Timedelta(minutes=10 * i))
                    .tz_convert("UTC")
                    .strftime("%Y-%m-%dT%H:%M")
                    for i in range(3)
                ],
                "shortwave_radiation": [100.0, None, 300.0],
            }
        }
        out = svc._resample(raw, self._index(15, 6), 15)
        assert out[-1] is None and out[-2] is None  # 上游还没出的时段

    async def test_整条都没有就不给曲线(self, monkeypatch):
        _install(monkeypatch, payload=_payload([]))
        async with AsyncClient() as http:
            assert await svc.series(http, _station(), self._index(15, 4), 15) is None

    async def test_上游失败不抛(self, monkeypatch):
        _install(monkeypatch, boom=RuntimeError("down"))
        async with AsyncClient() as http:
            assert await svc.series(http, _station(), self._index(15, 4), 15) is None


class TestLegacyResolution:
    """旧版客户端拿逐小时曲线时，卫星那条要跟着一起降。docs/06 §2.8"""

    def test_两条线降采样后仍等长同序(self):
        from app.curve_resolution import hourly_trend
        from app.schemas.home import TrendMetric, TrendPoint, TrendRange, TrendSeries

        base = pd.Timestamp("2026-09-20 00:00", tz=TZ)
        times = [(base + pd.Timedelta(minutes=15 * i)).isoformat() for i in range(9)]
        series = TrendSeries(
            metric=TrendMetric.RADIATION,
            unit="W/m²",
            range=TrendRange.H24,
            y_max=None,
            resolution_minutes=15,
            hub_height=None,
            points=[TrendPoint(time=t, value=float(i * 10)) for i, t in enumerate(times)],
            satellite=[TrendPoint(time=t, value=float(i * 12)) for i, t in enumerate(times)],
            satellite_source=svc.SOURCE,
        )
        out = hourly_trend(series)
        assert out.resolution_minutes == 60
        assert out.satellite is not None
        assert len(out.satellite) == len(out.points)
        assert [p.time for p in out.satellite] == [p.time for p in out.points]
        # 01:00 这一小时由 00:15…01:00 四格平均而来
        assert out.points[1].value == pytest.approx(25.0)
        assert out.satellite[1].value == pytest.approx(30.0)
