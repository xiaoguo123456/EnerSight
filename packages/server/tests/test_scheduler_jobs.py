"""预警扫描与报告预生成两个定时任务：分批取站点、并发口径、孤儿预警回扫。

这两个 job 此前没有测试 —— 它们用模块级的 SessionLocal，不走 conftest 的依赖覆盖，
所以这里自己换掉 SessionLocal，指向独立的内存库。
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import db as db_mod
from app.config import settings
from app.db import Base, id_pages, pages
from app.jobs import scheduler
from app.main import app
from app.models import Alert, CatalogPlant, Station


@pytest.mark.parametrize(
    ("general", "maps", "expected"),
    [
        (False, None, "disabled"),
        (False, True, "map_only"),
        (True, None, "all"),
        (True, False, "general_only"),
    ],
)
def test_地图调度可独立开启且默认兼容总开关(monkeypatch, general, maps, expected):
    monkeypatch.setattr(scheduler.settings, "enable_scheduler", general)
    monkeypatch.setattr(scheduler.settings, "enable_map_scheduler", maps)
    # 只验证注册任务，不启动真实计时器或出网预处理。
    monkeypatch.setattr(scheduler.AsyncIOScheduler, "start", lambda self: None)
    sched = scheduler.start(FastAPI())
    if expected == "disabled":
        assert sched is None
        return
    jobs = {job.id for job in sched.get_jobs()}
    if expected == "map_only":
        assert jobs == {"map_prepare"}
    else:
        assert "accumulate_generation" in jobs and "scan_alerts" in jobs
        assert ("map_prepare" in jobs) == (expected == "all")


@pytest.fixture
async def sessions(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # id_pages 读 app.db 的模块级名字，两个 job 读 scheduler 里 import 进来的那个
    monkeypatch.setattr(db_mod, "SessionLocal", factory)
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    app.state.http = httpx.AsyncClient(timeout=5.0)
    yield factory
    await app.state.http.aclose()
    await engine.dispose()


def _station(i: int) -> Station:
    return Station(
        id=f"st{i:04d}",
        owner_id="u",
        name=f"站{i}",
        type="solar",
        latitude=30.0 + i * 0.5,
        longitude=110.0 + i * 0.5,
        capacity_kw=500,
    )


def _plant(pid: str) -> CatalogPlant:
    return CatalogPlant(
        id=pid,
        source="gem",
        source_id=pid,
        type="solar",
        name=f"公开{pid}",
        latitude=36.5,
        longitude=100.5,
        capacity_kw=1000,
        status="operating",
    )


async def _seed(factory, stations: int = 0, plants: tuple[str, ...] = ()) -> None:
    async with factory() as db:
        for i in range(stations):
            db.add(_station(i))
        for pid in plants:
            db.add(_plant(pid))
        await db.commit()


class TestPaging:
    async def test_翻页不漏行不重复(self, sessions):
        await _seed(sessions, stations=7)
        async with sessions() as db:
            got: list[str] = []
            async for page in pages(db, select(Station), Station, 2):
                got.extend(s.id for s in page)
        assert got == sorted(got)
        assert len(got) == len(set(got)) == 7

    async def test_id翻页每页独立会话且不漏行(self, sessions):
        await _seed(sessions, stations=5)
        got: list[str] = []
        async for ids in id_pages(Station, 2):
            got.extend(ids)
        assert len(got) == len(set(got)) == 5

    async def test_空表不死循环(self, sessions):
        async with sessions() as db:
            assert [p async for p in pages(db, select(Station), Station, 10)] == []
        assert [p async for p in id_pages(Station, 10)] == []


class TestOrphanAlertPlants:
    """首页顺手扫过的公开电站留下了预警，不接着扫就永远解除不掉。"""

    async def _ids(self, factory) -> set[str]:
        async with factory() as db:
            rows = (await db.execute(scheduler._orphan_alert_plants())).scalars().all()
        return {p.id for p in rows}

    def _alert(self, station_id: str, active: bool) -> Alert:
        return Alert(
            station_id=station_id,
            kind="cloud",
            level="minor",
            title="t",
            description="d",
            active=active,
        )

    async def test_只捞仍有生效预警且不在站点表里的(self, sessions):
        await _seed(sessions, stations=1, plants=("cat-a", "cat-b", "cat-c"))
        async with sessions() as db:
            db.add(self._alert("cat-a", True))  # 孤儿，要捞
            db.add(self._alert("cat-b", False))  # 已解除，不捞
            db.add(self._alert("st0000", True))  # 个人站点，主循环已覆盖
            await db.commit()
        assert await self._ids(sessions) == {"cat-a"}

    async def test_没有预警时为空(self, sessions):
        await _seed(sessions, plants=("cat-a",))
        assert await self._ids(sessions) == set()

    async def test_同站多条生效预警不重复返回(self, sessions):
        await _seed(sessions, plants=("cat-a",))
        async with sessions() as db:
            db.add(self._alert("cat-a", True))
            db.add(self._alert("cat-a", True))
            await db.commit()
        assert await self._ids(sessions) == {"cat-a"}


class TestScanAlertsJob:
    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_forecast(_http, _lat, _lon, **_kw):
            return object()

        async def fake_scene(*_a, **_kw):
            return None

        monkeypatch.setattr(scheduler.weather, "get_forecast", fake_forecast)
        monkeypatch.setattr(scheduler.satellite, "load_scene_safely", fake_scene)

    async def test_个人站点与孤儿公开电站都扫到(self, sessions, monkeypatch: pytest.MonkeyPatch):
        await _seed(sessions, stations=2, plants=("cat-a",))
        async with sessions() as db:
            db.add(
                Alert(
                    station_id="cat-a",
                    kind="cloud",
                    level="minor",
                    title="t",
                    description="d",
                    active=True,
                )
            )
            await db.commit()
        scanned: list[str] = []

        async def fake_scan(_db, station, _fc, _sat):
            scanned.append(station.id)
            return 1

        monkeypatch.setattr(scheduler.alerts, "scan_station", fake_scan)
        monkeypatch.setattr(settings, "scan_batch_size", 1)  # 逼出多页
        await scheduler._make_scan_alerts(app)()
        assert scanned == ["st0000", "st0001", "cat-a"]

    async def test_单站失败不掀翻整轮(self, sessions, monkeypatch: pytest.MonkeyPatch):
        await _seed(sessions, stations=3)
        seen: list[str] = []

        async def flaky(_db, station, _fc, _sat):
            seen.append(station.id)
            if station.id == "st0001":
                raise RuntimeError("炸了")
            return 1

        monkeypatch.setattr(scheduler.alerts, "scan_station", flaky)
        await scheduler._make_scan_alerts(app)()
        assert seen == ["st0000", "st0001", "st0002"]


class TestGenerateReportsJob:
    async def test_并发受闸门限且每任务独立会话(self, sessions, monkeypatch: pytest.MonkeyPatch):
        await _seed(sessions, stations=6)
        monkeypatch.setattr(settings, "reports_concurrency", 3)
        monkeypatch.setattr(settings, "db_pool_size", 3)
        live = {"now": 0, "peak": 0}
        # 留住引用：只记 id() 的话，会话被回收后地址会被复用，判重会假阳性
        used: list[object] = []

        async def fake_generate(db, _http, _station):
            used.append(db)
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            try:
                await asyncio.sleep(0.02)
            finally:
                live["now"] -= 1

        monkeypatch.setattr(scheduler.reports, "generate_and_store", fake_generate)
        await scheduler._make_generate_reports(app)()
        assert live["peak"] > 1, "还是串行的，并发没生效"
        assert live["peak"] <= 3, "在途数超过了闸门"
        assert len(used) == len({id(x) for x in used}) == 6, "会话被复用了"

    async def test_并发上限被连接预算夹住(self, sessions, monkeypatch: pytest.MonkeyPatch):
        """线上 pool_size + max_overflow 一共 3 个连接，配再大也没用。"""
        await _seed(sessions, stations=6)
        monkeypatch.setattr(settings, "reports_concurrency", 64)
        monkeypatch.setattr(settings, "db_pool_size", 2)
        live = {"now": 0, "peak": 0}

        async def fake_generate(_db, _http, _station):
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            try:
                await asyncio.sleep(0.02)
            finally:
                live["now"] -= 1

        monkeypatch.setattr(scheduler.reports, "generate_and_store", fake_generate)
        await scheduler._make_generate_reports(app)()
        assert live["peak"] <= 2

    async def test_单站失败不掀翻整批(self, sessions, monkeypatch: pytest.MonkeyPatch):
        await _seed(sessions, stations=4)
        done: list[str] = []

        async def flaky(_db, _http, station):
            if station.id == "st0002":
                raise RuntimeError("AI 超时")
            done.append(station.id)

        monkeypatch.setattr(scheduler.reports, "generate_and_store", flaky)
        await scheduler._make_generate_reports(app)()
        assert sorted(done) == ["st0000", "st0001", "st0003"]

    async def test_翻页后站点被删不算失败(self, sessions, monkeypatch: pytest.MonkeyPatch):
        """翻页与生成之间隔着一段时间，站点可能已经没了。"""
        called: list[str] = []

        async def fake_generate(_db, _http, station):
            called.append(station.id)

        async def ghost_ids(*_a, **_kw):
            yield ["不存在的站点"]

        monkeypatch.setattr(scheduler.reports, "generate_and_store", fake_generate)
        monkeypatch.setattr(scheduler, "id_pages", ghost_ids)
        await scheduler._make_generate_reports(app)()  # 不抛异常
        assert called == []
