"""游客可看公开数据；自建场站与删除我的数据必须登录。docs/09 §4.3"""

import hashlib
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app import auth
from app.db import get_session
from app.main import app
from app.models import Alert, DailyGeneration, Report, Station
from app.render import tiles
from app.services import prediction_archive
from tests.test_stations import SUZHOU


@pytest.fixture
def production(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "wx_appid", "wx-test")


def bearer(openid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth.issue_token(openid)[0]}"}


async def _own(client: AsyncClient, openid: str) -> str:
    r = await client.post("/v1/stations", json=SUZHOU, headers=bearer(openid))
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def test_游客可浏览公开目录(client: AsyncClient, production):
    assert (await client.get("/v1/stations/public")).status_code == 200
    r = await client.get("/v1/stations/catalog", params={"keyword": "苏州"})
    assert r.status_code == 200


async def test_游客不能读写自建场站(client: AsyncClient, production):
    r = await client.get("/v1/stations")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    assert (await client.post("/v1/stations", json=SUZHOU)).status_code == 401
    sid = await _own(client, "owner-a")
    r = await client.get("/v1/trends", params={"station_id": sid})
    assert r.status_code == 401 and r.json()["error"]["code"] == "LOGIN_REQUIRED"
    r = await client.get("/v1/trends", params={"station_id": sid}, headers=bearer("owner-b"))
    assert r.status_code == 403


async def test_公开接口带失效令牌返回401便于续登(client: AsyncClient, production):
    r = await client.get("/v1/alerts/current", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_删除我的数据只删本人场站及关联记录(
    client: AsyncClient, production, tmp_path, monkeypatch
):
    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    mine, other = await _own(client, "owner-a"), await _own(client, "owner-b")
    gen = app.dependency_overrides[get_session]()
    db = await gen.__anext__()
    try:
        for sid in (mine, other):
            db.add(Alert(station_id=sid, kind="wind", level="minor", title="t", description="d"))
            db.add(DailyGeneration(station_id=sid, day=date(2026, 9, 14), kwh=1.0))
            db.add(
                Report(
                    station_id=sid,
                    day=date(2026, 9, 14),
                    content={},
                    summary={},
                    provider="rule",
                    prompt_input="",
                )
            )
        await db.commit()
    finally:
        await gen.aclose()
    folder = tmp_path / "prediction-outlook" / "2026-09-14"
    folder.mkdir(parents=True)
    for sid in (mine, other):
        (folder / f"{sid}.json").write_text(f'{{"station_id": "{sid}", "prediction": {{}}}}')

    assert (await client.delete("/v1/me")).status_code == 401
    assert (await client.delete("/v1/me", headers=bearer("owner-a"))).status_code == 204

    gen = app.dependency_overrides[get_session]()
    db = await gen.__anext__()
    try:
        for column in (Station.id, Alert.station_id, DailyGeneration.station_id, Report.station_id):
            ids = set((await db.execute(select(column))).scalars().all())
            assert mine not in ids and other in ids
    finally:
        await gen.aclose()
    assert [p.name for p in folder.iterdir()] == [f"{other}.json"]


def test_滚动预测留档按文件名前缀删除(tmp_path, monkeypatch):
    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    folder = tmp_path / "prediction-archive" / "2026-09-14"
    folder.mkdir(parents=True)
    key = hashlib.sha256(b"abc123def456:best_match").hexdigest()[:24]
    (folder / f"{key}-2026-09-14-08-x.json").write_text("{}")
    (folder / f"{'0' * 24}-2026-09-14-08-x.json").write_text("{}")
    assert prediction_archive.purge_station_archives({"abc123def456"}) == 1
    assert [p.name[:24] for p in folder.iterdir()] == ["0" * 24]
