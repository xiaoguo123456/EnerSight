"""预热坐标清单的导出：只出公开目录、去重、跳过省级占位、原子替换。

自建实例的冷读成本是每个新坐标 5–6 秒，预热要在实例本机跑（北京→实例的大响应只有
16–18 KB/s）。这里锁住「清单里有什么、没有什么」—— 多出自建站点就是把私有数据写进
公开静态目录，少了坐标就是页面照样冷。见 docs/2026-09-16-open-meteo-self-host.md
"""

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.jobs import warm_coords
from app.models import CatalogPlant, Station


@pytest.fixture
async def store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(warm_coords, "SessionLocal", factory)
    monkeypatch.setattr(warm_coords, "tile_dir", lambda: tmp_path)
    yield factory
    await engine.dispose()


def _plant(pid: str, lat, lon, provenance=None) -> CatalogPlant:
    return CatalogPlant(
        id=pid,
        source="gem",
        source_id=pid.split(":")[-1],
        name=pid,
        type="solar",
        capacity_kw=1000.0,
        latitude=lat,
        longitude=lon,
        provenance=provenance,
    )


def _read(tmp_path) -> dict:
    return json.loads((tmp_path / warm_coords.FILENAME).read_text(encoding="utf-8"))


async def test_去重且按序输出(store, tmp_path):
    async with store() as db:
        db.add_all(
            [
                _plant("gem:b", 31.5, 120.5),
                _plant("gem:a", 20.25, 100.75),
                _plant("gem:dup", 31.5, 120.5),  # 同址多期：只算一个坐标
            ]
        )
        await db.commit()
    assert await warm_coords.export() == 2
    assert _read(tmp_path)["coords"] == [[20.25, 100.75], [31.5, 120.5]]


async def test_跳过省级占位但保留有推断方法的(store, tmp_path):
    """省级占位本来就不参与发电预测，预热它们是白花时间。判定与 catalog_basis 同源。"""
    async with store() as db:
        db.add_all(
            [
                _plant("gem:ok", 31.5, 120.5),
                _plant("gem:prov", 40.0, 110.0, {"location": {"tier": "province"}}),
                _plant("gem:kept", 41.0, 111.0, {"location": {"tier": "province", "method": "县"}}),
            ]
        )
        await db.commit()
    assert await warm_coords.export() == 2
    assert _read(tmp_path)["coords"] == [[31.5, 120.5], [41.0, 111.0]]


async def test_不导出自建站点(store, tmp_path):
    """自建站点是私有数据，不能落到公开静态目录；它们本来也被预警扫描拉热。"""
    async with store() as db:
        db.add(_plant("gem:public", 31.5, 120.5))
        db.add(
            Station(
                id="mine-1",
                owner_id="someone",
                name="我的电站",
                type="solar",
                capacity_kw=5000.0,
                latitude=45.678,
                longitude=125.432,
            )
        )
        await db.commit()
    await warm_coords.export()
    coords = _read(tmp_path)["coords"]
    assert coords == [[31.5, 120.5]]
    assert [45.678, 125.432] not in coords


async def test_原子替换不留半个文件(store, tmp_path):
    async with store() as db:
        db.add(_plant("gem:ok", 31.5, 120.5))
        await db.commit()
    await warm_coords.export()
    await warm_coords.export()  # 再导一次，覆盖而不是追加
    assert _read(tmp_path)["coords"] == [[31.5, 120.5]]
    assert not list(tmp_path.glob("*.tmp")), "临时文件必须清掉"
