"""卫星辐照实况。docs/19 §四"""

from datetime import UTC, datetime, timedelta

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
