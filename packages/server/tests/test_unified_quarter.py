"""统一 15 分钟的请求、时间边界、电量守恒和低内存留档回归。"""

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from app.errors import UpstreamUnavailable
from app.models import Station
from app.providers.open_meteo import MINUTELY_FIELDS
from app.services import energy, prediction, weather
from app.services.home import build_current_weather
from app.services.weather_cells import WeatherCells
from tests.fixtures_forecast import TZ, make_forecast


@pytest.fixture
def quarter(monkeypatch):
    now = datetime.now(ZoneInfo(TZ)).replace(hour=6, minute=37, second=0, microsecond=0)
    monkeypatch.setattr(weather.Forecast, "now", lambda self: now)
    yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, tzinfo=None)
    raw = make_forecast(start_date=yesterday, wind_today=15)
    raw.pop("hourly")
    return raw


def station(kind="solar", **kwargs):
    return Station(
        id="15分钟验收站", type=kind, latitude=31.3, longitude=120.62, capacity_kw=1000, **kwargs
    )


async def test_并发消费者仅请求完整15分钟且跨日重新取数(quarter, monkeypatch):
    weather.clear_cache()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta.json").respond(404)
        route = mock.get(url__regex=r".*/v1/forecast.*").respond(200, json=quarter)
        async with httpx.AsyncClient() as http:
            results = await asyncio.gather(
                *(weather.get_forecast(http, 31.3, 120.62) for _ in range(8))
            )
            assert route.call_count == 1
            assert all(fc is results[0] for fc in results)
            params = route.calls[0].request.url.params
            assert "hourly" not in params
            assert set(params["minutely_15"].split(",")) == set(MINUTELY_FIELDS)
            assert params["past_days"] == "1" and params["forecast_days"] == "8"
            assert results[0].hourly.empty and results[0].step_minutes == 15
            tomorrow = results[0].now() + timedelta(days=1)
            monkeypatch.setattr(weather.Forecast, "now", lambda self: tomorrow)
            await weather.get_forecast(http, 31.3, 120.62)
            assert route.call_count == 2
    weather.clear_cache()


def test_当前时刻和昨日同期对齐15分钟(quarter):
    fc = weather.parse_forecast(quarter, require_quarter=True)
    assert fc.current_hour().strftime("%H:%M") == "06:30"
    assert fc.current_interval().strftime("%H:%M") == "06:45"
    fc.data.loc[fc.current_hour(), "wind_speed_10m"] = 6
    fc.data.loc[fc.current_hour() - pd.Timedelta(days=1), "wind_speed_10m"] = 3
    current = build_current_weather(fc)
    assert current.observed_at.endswith("06:30:00+08:00")
    assert current.wind_speed.value == 6 and current.wind_speed.delta_percent == 100
    assert current.radiation.value == fc.data.loc[fc.current_interval(), "shortwave_radiation"]
    for kind in ("solar", "wind"):
        snapshot = energy.compute(station(kind), fc)
        assert snapshot.current_kw is not None
        assert snapshot.current_kw == snapshot.hourly_kw.loc[energy.current_label(fc, kind)]


@pytest.mark.parametrize("kind", ["solar", "wind"])
def test_七天逐日96点且电量按四分之一小时积分(quarter, kind):
    fc = weather.parse_forecast(quarter, require_quarter=True)
    out = prediction.compute_days(station(kind), fc, 7)
    assert len(out.days) == 7
    for day in out.days:
        assert day.resolution_minutes == 15 and len(day.power_kw) == 96
        assert day.power_kw[0].time.endswith("T00:00:00+08:00")
        assert day.power_kw[-1].time.endswith("T23:45:00+08:00")
        assert day.energy_kwh == pytest.approx(sum(p.value for p in day.power_kw) * 0.25, abs=0.03)
    assert prediction.compute(station(kind), fc).energy_kwh == out.days[0].energy_kwh


def test_午夜末区间仍有功率且跨夜限电不扩大四倍(quarter, monkeypatch):
    fc = weather.parse_forecast(quarter)
    now = fc.now().replace(hour=23, minute=59)
    monkeypatch.setattr(weather.Forecast, "now", lambda self: now)
    assert fc.current_interval().date() == now.date() + timedelta(days=1)
    assert energy.compute(station(), fc).current_kw == 0
    from app.services.curtailment import apply, parse

    rule = parse(
        {"mode": "schedule", "windows": [{"start_hour": 23, "end_hour": 1, "limit_percent": 50}]}
    )
    labels = fc.today().index
    baseline = pd.Series(1000.0, index=labels)
    limited = apply(baseline, rule, 1000, interval_end=False, step_minutes=15)
    assert (limited == 500).sum() == 8
    assert (baseline - limited).sum() * 0.25 == 1000


def test_必要辐射长缺测不变成零电量(quarter):
    fc = weather.parse_forecast(quarter)
    start = fc.current_hour().normalize() + pd.Timedelta(hours=9)
    for key in ("shortwave_radiation", "diffuse_radiation", "direct_normal_irradiance"):
        fc.data.loc[start : start + pd.Timedelta(hours=4), key] = np.nan
    result = prediction.compute(station(), fc)
    assert result.energy_kwh is None and all(p.value is None for p in result.power_kw)


def test_线上禁止缺少15分钟时使用小时响应(quarter):
    with pytest.raises(UpstreamUnavailable):
        weather.parse_forecast({"timezone": TZ, "hourly": {"time": []}}, require_quarter=True)


def test_网格缓存逐块读写且坏文件可修复(tmp_path, quarter):
    cells = WeatherCells(tmp_path)
    for i in range(20):
        cells[str(i)] = {**quarter, "latitude": float(i)}
    assert len(cells._recent) == 8
    restored = WeatherCells(tmp_path, cells.references)
    assert restored["0"]["latitude"] == 0
    path = tmp_path / f"{cells.references['0']}.json"
    path.write_text("损坏")
    restarted = WeatherCells(tmp_path, cells.references)
    assert "0" not in restarted
    restarted["0"] = {**quarter, "latitude": 0.0}
    assert WeatherCells(tmp_path, restarted.references)["0"]["latitude"] == 0


def test_流式网格留档指纹一致且不保存路径(tmp_path, quarter, monkeypatch):
    from app.render import tiles
    from app.services import fleet_prediction, prediction_archive

    monkeypatch.setattr(tiles, "tile_dir", lambda: tmp_path / "tiles")
    cells = WeatherCells(tmp_path / "cells")
    cells["31.3,120.6"] = quarter
    snapshot = fleet_prediction.blank("gfs_global", datetime.now(ZoneInfo(TZ)).date().isoformat())
    first = prediction_archive.save_fleet_inputs(snapshot, [], cells, {}, {})
    second = prediction_archive.save_fleet_inputs(snapshot, [], cells, {}, {})
    assert first == second
    path = tmp_path / "prediction-fleet-inputs" / f"{first}.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()[:24] == path.stem
    assert json.loads(raw)["weather_cells"]["31.3,120.6"] == quarter
    assert not list(path.parent.glob("*.tmp"))


def test_过期网格清理不影响近期缓存其他模型或留档(tmp_path):
    cells = WeatherCells(tmp_path / "gfs_global" / "2026-09-13")
    expired = tmp_path / "gfs_global" / "2026-09-10"
    keep = [
        cells.folder,
        tmp_path / "gfs_global" / "2026-09-11",
        tmp_path / "gfs_global" / "prediction-fleet-inputs",
        tmp_path / "icon_global" / "2026-09-10",
    ]
    for folder in [expired, *keep]:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "sample.json").write_text("{}")
    cells.prune_before(date(2026, 9, 11))
    assert not expired.exists()
    assert all((folder / "sample.json").exists() for folder in keep)
