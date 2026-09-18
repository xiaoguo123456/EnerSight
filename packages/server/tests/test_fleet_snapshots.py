"""计算完成后再发布，刷新和跨日不能清空已可用的预测。"""

import asyncio
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
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
    monkeypatch.setattr(fleet, "catalog_revision", AsyncMock(side_effect=RuntimeError("连接超时")))
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


# ───────────────────── 每天只拉一次：批次、时效与跨日 ─────────────────────


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """独立的任务表与快照目录；目录查询与重算替换为桩，只看是否开新一轮。"""
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    monkeypatch.setattr(fleet, "_jobs", {})
    monkeypatch.setattr(fleet, "_checked", {})
    monkeypatch.setattr(fleet, "catalog_revision", AsyncMock(return_value="1:"))
    monkeypatch.setattr(fleet, "operating_plants", AsyncMock(return_value=[]))
    build = AsyncMock()
    monkeypatch.setattr(fleet, "build", build)
    return build


@pytest.mark.parametrize(
    "status,minutes,detail,rebuilt",
    [
        ("ready", 13 * 60, True, False),  # 旧规则 12 小时后整轮重拉
        ("partial", 10, True, False),
        ("partial", 31, True, True),  # 部分覆盖半小时后续算，只补拉缺失坐标
        # 逐省明细缺失（本功能上线前算的快照）补一轮，否则筛地区会一直停在准备中
        ("ready", 10, False, True),
    ],
)
async def test_当天快照完成后不再重算_部分覆盖才续算(
    tmp_path, isolated, status, minutes, detail, rebuilt
):
    saved = snapshot()
    saved.update(
        status=status,
        catalog_revision="1:",
        generated_at=(datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(),
    )
    fleet.write(tmp_path / f"gfs_global-{fleet.day_key()}.json", saved)
    if detail:
        fleet.write(fleet.regions_path("gfs_global", fleet.day_key()), {"regions": {}})
    await fleet.ensure(None, "gfs_global")
    await fleet._jobs[f"gfs_global-{fleet.day_key()}"]
    assert isolated.await_count == (1 if rebuilt else 0)


@pytest.mark.parametrize("hour,started", [(7, False), (8, True)])
async def test_跨日在额度重置前沿用上一日预测(tmp_path, monkeypatch, isolated, hour, started):
    now = datetime.now(fleet.ZoneInfo(fleet.TZ)).replace(hour=hour, minute=30)
    monkeypatch.setattr(fleet, "beijing_now", lambda: now)
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    fleet.write(tmp_path / f"gfs_global-{yesterday}.json", snapshot(yesterday))
    result = await fleet.ensure(None, "gfs_global")
    key = f"gfs_global-{now.date().isoformat()}"
    assert result.date == now.date().isoformat() and result.energy_kwh == 4800
    assert (key in fleet._jobs) is started
    if started:
        await fleet._jobs[key]
        assert isolated.await_count == 1


async def test_续拉只请求缺失坐标且每天轮数有上限(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    monkeypatch.setattr(settings, "fleet_coords_per_request", 1)
    monkeypatch.setattr(settings, "fleet_fetch_rounds_per_day", 2)
    raw = forecast()
    seen = []

    def respond(request):
        latitude = request.url.params["latitude"]
        seen.append(latitude)
        # 用 5xx 模拟单个坐标持续失败；429 会进入共享冷却，干扰其他用例
        return httpx.Response(500 if latitude.startswith("40.") else 200, json=raw)

    plants = [plant("a"), plant("c", lat=40.3)]
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta\.json").respond(404)
        mock.get(url__regex=r".*/v1/forecast.*").mock(side_effect=respond)
        async with httpx.AsyncClient() as http:
            for _ in range(3):
                await fleet.build(http, "gfs_global", fleet.day_key(), plants)
    # 第一轮两个坐标都请求；第二轮只补失败的坐标；第三轮轮数用完不再出网
    assert len(seen) == 3
    assert not seen[0].startswith("40.") and seen[1].startswith("40.") and seen[2] == seen[1]
    out = fleet.load(tmp_path / f"gfs_global-{fleet.day_key()}.json")
    assert out["covered_count"] == 1 and out["status"] == "partial"


class Test八点门槛只为官方额度而设:
    """`fleet_refresh_hour` 是为了等 Open-Meteo 日额度在 UTC 零点（北京 08:00）重置。

    自建主源不占官方额度，就没有等这个整点的理由。跨日保护另按 day_key 判定，
    与额度无关，不受这里影响。见 docs/2026-09-16-open-meteo-self-host.md 第十一节。
    """

    def _carried_yesterday(self, tmp_path, monkeypatch) -> str:
        """造一份「昨日快照被顺延到今天」的局面，返回今天的任务键。

        `_jobs` / `_checked` 是模块级的，必须换成独立的字典 —— 否则别的用例留下的
        任务会让这里的断言随收集顺序飘（CI 上就这么红过一次）。同 `isolated` 夹具。
        """
        monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
        monkeypatch.setattr(fleet, "_jobs", {})
        monkeypatch.setattr(fleet, "_checked", {})
        # **先钉时钟再算日期**：day_key() 读 beijing_now()，顺序反了的话「昨天的快照」
        # 会正好落在钉住后的今天上，carry_previous 不触发、门槛也就不适用 ——
        # 这个测试在 2026-09-17 能过、09-18 必红，CI 就是这么红的。
        monkeypatch.setattr(fleet, "beijing_now", lambda: datetime(2026, 9, 17, 1, 0, tzinfo=UTC))
        monkeypatch.setattr(fleet.settings, "fleet_refresh_hour", 23)  # 保证「还没到点」
        today = fleet.day_key()
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        fleet.write(tmp_path / f"gfs_global-{yesterday}.json", snapshot(yesterday))
        return f"gfs_global-{today}"

    async def test_用官方时到点前不开新轮(self, tmp_path, monkeypatch):
        key = self._carried_yesterday(tmp_path, monkeypatch)
        monkeypatch.setattr(fleet.settings, "open_meteo_fallback_base", "")
        await fleet.ensure(None, "gfs_global")
        assert key not in fleet._jobs, "到点前不该起后台轮次"

    async def test_自建时不等到点直接开轮(self, tmp_path, monkeypatch):
        key = self._carried_yesterday(tmp_path, monkeypatch)
        monkeypatch.setattr(
            fleet.settings, "open_meteo_fallback_base", "https://api.open-meteo.com/v1"
        )
        monkeypatch.setattr(fleet, "catalog_revision", AsyncMock(side_effect=RuntimeError("停")))
        await fleet.ensure(None, "gfs_global")
        assert key in fleet._jobs, "自建不占官方额度，不该干等"
        with suppress(Exception):
            await fleet._jobs[key]
