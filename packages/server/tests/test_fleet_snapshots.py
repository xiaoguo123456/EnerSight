"""计算完成后再发布，刷新和跨日不能清空已可用的预测。"""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.config import settings
from app.schemas.common import Coord
from app.services import fleet_prediction as fleet
from app.services import home
from tests.test_prediction import forecast, plant, station


def snapshot(day=None):
    day = day or fleet.day_key()
    result = fleet.blank("gfs_global", day).model_dump()
    result.update(status="ready", energy_kwh=2400, covered_count=1, eligible_count=1)
    result["days"] = [
        dict(
            date=(date.fromisoformat(day) + timedelta(days=k)).isoformat(),
            weekday=1,
            lead_days=k,
            energy_kwh=2400 * (k + 1),
            solar_kwh=0,
            wind_kwh=2400 * (k + 1),
            power_kw=[],
            covered_count=1,
            covered_capacity_kw=1000,
            status="ready",
            resolution_minutes=15,
        )
        for k in range(7)
    ]
    return result


@pytest.fixture(autouse=True)
async def cleanup():
    await fleet.shutdown()
    yield
    await fleet.shutdown()


async def test_后台排队时接口立即返回已有结果(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    saved = snapshot()
    path = tmp_path / f"gfs_global-{fleet.day_key()}.json"
    fleet.write(path, saved)
    gate = asyncio.Lock()
    await gate.acquire()
    monkeypatch.setattr(fleet, "_gate", gate)
    first = await asyncio.wait_for(fleet.ensure(None, "gfs_global"), timeout=0.1)
    second = await fleet.ensure(None, "gfs_global")
    assert first.energy_kwh == second.energy_kwh == 2400
    assert first.updating and second.updating
    assert fleet.load(path) == saved
    assert len(fleet._jobs) == 1


def test_跨日沿用真实日期而非昨日电量(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    yesterday = (date.fromisoformat(fleet.day_key()) - timedelta(days=1)).isoformat()
    fleet.write(tmp_path / f"gfs_global-{yesterday}.json", snapshot(yesterday))
    carried = fleet.carry_previous("gfs_global", fleet.day_key())
    assert carried["date"] == fleet.day_key()
    assert carried["energy_kwh"] == 4800
    assert carried["days"][0]["date"] == fleet.day_key()
    assert carried["days"][0]["lead_days"] == 0
    assert carried["days"][-1]["energy_kwh"] is None
    assert carried["days"][-1]["power_kw"] == []
    assert carried["_carried"]


async def test_更新失败保留数据和时间并退避(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    path = tmp_path / f"gfs_global-{fleet.day_key()}.json"
    saved = snapshot()
    fleet.write(path, saved)
    monkeypatch.setattr(
        fleet.weather, "get_model_meta", AsyncMock(side_effect=RuntimeError("连接超时"))
    )
    await fleet.ensure(None, "gfs_global")
    await fleet._jobs[f"gfs_global-{fleet.day_key()}"]
    result = await fleet.ensure(None, "gfs_global")
    assert result.energy_kwh == 2400 and result.status == "ready"
    assert result.generated_at == saved["generated_at"]
    assert not result.updating
    assert fleet.load(path)["_retry_at"] > 0


@pytest.mark.parametrize("existing", [False, True])
async def test_中途不发布部分结果完成后整体替换(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    path = tmp_path / f"gfs_global-{fleet.day_key()}.json"
    if existing:
        fleet.write(path, snapshot())
    writes = []
    real_write = fleet.write

    def tracked(destination, value):
        if destination == path:
            writes.append(value.copy())
        real_write(destination, value)

    monkeypatch.setattr(fleet, "write", tracked)
    real_calculate = fleet.calculate_cell

    def calculate(*args):
        assert not writes
        assert fleet.load(path) is None if not existing else fleet.load(path)["energy_kwh"] == 2400
        return real_calculate(*args)

    monkeypatch.setattr(fleet, "calculate_cell", calculate)
    with respx.mock:
        respx.get(url__regex=r".*open-meteo.*").mock(
            return_value=httpx.Response(200, json=forecast())
        )
        async with httpx.AsyncClient() as http:
            await fleet.build(http, "gfs_global", fleet.day_key(), [plant("完成测试")])
    assert writes and all(row["status"] in ("ready", "partial") for row in writes)
    assert all(row["covered_count"] == 1 for row in writes)
    assert len(fleet.load(path)["days"]) == settings.forecast_outlook_days


@pytest.mark.parametrize("existing", [False, True])
async def test_整轮限流保留有效快照且退避(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    path = tmp_path / f"gfs_global-{fleet.day_key()}.json"
    if existing:
        fleet.write(path, snapshot())
    with respx.mock:
        respx.get(url__regex=r".*open-meteo.*").mock(return_value=httpx.Response(429))
        async with httpx.AsyncClient() as http:
            await fleet.build(http, "gfs_global", fleet.day_key(), [plant("限流测试")])
    assert fleet.load(path)["energy_kwh"] == (2400 if existing else None)
    assert fleet.load(path)["_retry_at"] > 0


async def test_等气象之前归还场站查询的数据库连接(monkeypatch):
    db = AsyncMock()

    async def blocked(*args):
        db.commit.assert_awaited_once()
        raise RuntimeError("测试停在气象等待处")

    monkeypatch.setattr(home.weather, "get_forecast", blocked)
    with pytest.raises(RuntimeError, match="测试停在"):
        await home.build_station_view(None, station(), Coord.WGS84, db)
