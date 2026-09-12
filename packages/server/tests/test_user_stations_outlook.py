"""自建场站、出力约束、7 天预测与起报时间。docs/17"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import DailyGeneration, Station
from app.services import accumulate, curtailment, model_resolution, prediction, weather
from app.services import fleet_prediction as fleet
from tests.fixtures_forecast import TZ, make_forecast

SUZHOU = {
    "name": "苏州光伏站",
    "type": "solar",
    "latitude": 31.30,
    "longitude": 120.62,
    "capacity": 500,
}
WIND = {
    "name": "广东风电站",
    "type": "wind",
    "latitude": 21.75,
    "longitude": 111.95,
    "capacity": 2000,
}
META = {
    "last_run_initialisation_time": 1789106400,  # 2026-09-11 06:00Z
    "last_run_availability_time": 1789132166,
    "update_interval_seconds": 21600,
}


def _yesterday_midnight() -> datetime:
    now = datetime.now(ZoneInfo(TZ))
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def _forecast(**kw) -> dict:
    return make_forecast(start_date=_yesterday_midnight(), **kw)


@pytest.fixture(autouse=True)
def _fresh_cache():
    weather.clear_cache()
    yield
    weather.clear_cache()


@pytest.fixture
def open_meteo():
    """预报走合成数据，元数据 404：起报时间为 null 但主链路不受影响。"""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta\.json").mock(return_value=Response(404))
        yield mock.get(url__regex=r".*/v1/forecast.*").mock(
            return_value=Response(200, json=_forecast(wind_today=15))
        )


# ────────────────────────────── 出力约束（限电第一层） ──────────────────────────────


def _series(interval_end: bool) -> pd.Series:
    # 2026-09-14 是周一；区间末标签从 01:00 起，起点标签从 00:00 起
    start = "2026-09-14 01:00" if interval_end else "2026-09-14 00:00"
    idx = pd.date_range(start, periods=24, freq="h", tz=TZ)
    return pd.Series(1000.0, index=idx)


def test_固定比例折减():
    rule = curtailment.parse({"mode": "ratio", "ratio_percent": 20})
    out = curtailment.apply(_series(False), rule, 1000, interval_end=False)
    assert (out == 800).all()


def test_分时段封顶按墙钟起点匹配_区间末标签减一小时():
    rule = curtailment.parse(
        {"mode": "schedule", "windows": [{"start_hour": 11, "end_hour": 14, "limit_percent": 50}]}
    )
    end_labels = curtailment.apply(_series(True), rule, 1000, interval_end=True)
    by_hour = {ts.hour: v for ts, v in end_labels.items()}
    # 标签 12/13/14 对应墙钟 11/12/13 → 封顶；标签 11、15 不动
    assert by_hour[12] == by_hour[13] == by_hour[14] == 500
    assert by_hour[11] == by_hour[15] == 1000
    start_labels = curtailment.apply(_series(False), rule, 1000, interval_end=False)
    by_hour = {ts.hour: v for ts, v in start_labels.items()}
    assert by_hour[11] == by_hour[13] == 500 and by_hour[14] == 1000


def test_星期与跨零点时段():
    weekend = curtailment.parse(
        {
            "mode": "schedule",
            "windows": [{"start_hour": 0, "end_hour": 24, "limit_percent": 0, "weekdays": [6, 7]}],
        }
    )
    assert (curtailment.apply(_series(False), weekend, 1000, interval_end=False) == 1000).all()
    night = curtailment.parse(
        {"mode": "schedule", "windows": [{"start_hour": 22, "end_hour": 6, "limit_percent": 10}]}
    )
    out = curtailment.apply(_series(False), night, 1000, interval_end=False)
    by_hour = {ts.hour: v for ts, v in out.items()}
    assert by_hour[23] == by_hour[0] == by_hour[5] == 100 and by_hour[6] == by_hour[21] == 1000


def test_缺测保留_无规则原样返回():
    s = _series(False)
    s.iloc[3] = np.nan
    rule = curtailment.parse({"mode": "ratio", "ratio_percent": 50})
    assert np.isnan(curtailment.apply(s, rule, 1000, interval_end=False).iloc[3])
    assert curtailment.parse(None) is None and curtailment.parse({"mode": "none"}) is None
    assert curtailment.apply(s, None, 1000, interval_end=False) is s


# ────────────────────────────── 站点接口 ──────────────────────────────


async def _create(client: AsyncClient, body: dict, **params) -> dict:
    r = await client.post("/v1/stations", json=body, params=params)
    assert r.status_code == 201, r.text
    return r.json()["data"]


class TestStationApi:
    async def test_自建站点带出力约束并标记为本人所有(self, client: AsyncClient):
        d = await _create(client, {**WIND, "curtailment": {"mode": "ratio", "ratio_percent": 15}})
        assert d["is_own"] is True
        assert d["curtailment"] == {"mode": "ratio", "ratio_percent": 15.0, "windows": []}
        assert d["metrics"]["grid_generation"] is None  # 未跑任务前为 null

    @pytest.mark.parametrize(
        "bad",
        [
            {"mode": "schedule", "windows": []},
            {"mode": "ratio"},
            {"mode": "ratio", "ratio_percent": 120},
            {
                "mode": "schedule",
                "windows": [
                    {"start_hour": 9, "end_hour": 12, "limit_percent": 50, "weekdays": [8]}
                ],
            },
        ],
    )
    async def test_约束校验(self, client: AsyncClient, bad):
        r = await client.post("/v1/stations", json={**WIND, "curtailment": bad})
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM"

    async def test_修改与清除约束(self, client: AsyncClient):
        d = await _create(client, WIND)
        assert d["curtailment"] is None
        windows = [{"start_hour": 11, "end_hour": 14, "limit_percent": 60, "weekdays": [1, 2, 3]}]
        rule = {"mode": "schedule", "windows": windows}
        r = await client.patch(f"/v1/stations/{d['id']}", json={"curtailment": rule})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["curtailment"]["windows"] == windows
        r = await client.patch(f"/v1/stations/{d['id']}", json={"name": "改名"})
        assert r.json()["data"]["curtailment"]["mode"] == "schedule"  # 不传保持
        r = await client.patch(f"/v1/stations/{d['id']}", json={"curtailment": None})
        assert r.json()["data"]["curtailment"] is None  # 传 null 清除

    async def test_每用户上限(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr(settings, "max_stations_per_user", 2)
        await _create(client, SUZHOU)
        await _create(client, WIND)
        r = await client.post("/v1/stations", json={**WIND, "name": "第三座"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "STATION_LIMIT"
        assert (await client.get("/v1/stations")).json()["data"]["counts"]["all"] == 2


# ────────────────────────────── 预测：可发与上网两个口径 ──────────────────────────────


def _wind_station(rule: dict | None = None) -> Station:
    return Station(
        id="abc123def456",
        owner_id="u",
        type="wind",
        latitude=31.3,
        longitude=120.6,
        capacity_kw=1000,
        hub_height=100,
        tilt=None,
        azimuth=None,
        curtailment=rule,
    )


def test_有规则时给上网口径_无规则一律null():
    fc = weather.parse_forecast(_forecast(wind_today=15))
    plain = prediction.compute(_wind_station(), fc)
    assert plain.grid_energy_kwh is None and plain.grid_power_kw is None
    assert plain.assumptions[0] == "未计入限电、检修及故障影响"
    limited = prediction.compute(_wind_station({"mode": "ratio", "ratio_percent": 25}), fc)
    assert limited.energy_kwh == pytest.approx(plain.energy_kwh)  # 可发电量不变
    assert limited.grid_energy_kwh == pytest.approx(plain.energy_kwh * 0.75, rel=1e-6)
    assert limited.curtailed_kwh == pytest.approx(plain.energy_kwh * 0.25, rel=1e-4)
    assert len(limited.grid_power_kw) == 24
    assert "25%" in limited.assumptions[0]


def test_未来7天逐日预测_指数不受限电影响():
    fc = weather.parse_forecast(_forecast(wind_today=15))
    out = prediction.compute_days(_wind_station({"mode": "ratio", "ratio_percent": 50}), fc, 7)
    assert [d.lead_days for d in out.days] == list(range(7))
    today = fc.current_hour().date()
    assert [d.date for d in out.days] == [(today + timedelta(days=k)).isoformat() for k in range(7)]
    assert all(d.energy_kwh is not None and d.index_score is not None for d in out.days)
    assert all(d.grid_energy_kwh == pytest.approx(d.energy_kwh * 0.5, rel=1e-6) for d in out.days)
    # 15 m/s 满发：容量因子 = 1 − 场站损耗 → 指数由容量因子分档，不因限电减半而变
    assert out.days[0].index_score == out.days[3].index_score
    assert out.days[0].weekday == date.fromisoformat(out.days[0].date).isoweekday()
    assert out.basis is not None and out.basis.issued_at is None  # 无元数据：不猜
    assert any("中期参考" in a for a in out.assumptions)


async def test_单站7天接口(client: AsyncClient, open_meteo):
    d = await _create(client, {**WIND, "curtailment": {"mode": "ratio", "ratio_percent": 10}})
    r = await client.get("/v1/predictions/station", params={"station_id": d["id"], "days": 7})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert len(body["days"]) == 7 and body["station_id"] == d["id"]
    assert body["days"][0]["grid_energy_kwh"] == pytest.approx(body["days"][0]["energy_kwh"] * 0.9)
    assert body["basis"]["fetched_at"] and body["basis"]["issued_at"] is None
    r = await client.get("/v1/predictions/station", params={"station_id": d["id"], "days": 9})
    assert r.status_code == 400


async def test_首页预测带批次信息与上网电量(client: AsyncClient, open_meteo):
    d = await _create(client, {**WIND, "curtailment": {"mode": "ratio", "ratio_percent": 10}})
    body = (await client.get("/v1/home", params={"station_id": d["id"]})).json()["data"]
    p = body["prediction"]
    assert p["basis"]["model"] == "best_match" and p["basis"]["resolved_model"] == "ecmwf_ifs"
    assert p["grid_energy_kwh"] == pytest.approx(p["energy_kwh"] * 0.9)
    assert body["station"]["metrics"]["grid_generation"] == pytest.approx(
        body["station"]["metrics"]["daily_generation"] * 0.9, abs=0.2
    )
    detail = (await client.get(f"/v1/stations/{d['id']}/detail")).json()["data"]
    assert detail["basis"]["fetched_at"]


# ────────────────────────────── 起报时间与自动选择复核 ──────────────────────────────


async def test_起报时间来自模型元数据_拿不到不猜():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/data/ecmwf_ifs/static/meta\.json").mock(
            return_value=Response(200, json=META)
        )
        mock.get(url__regex=r".*/v1/forecast.*").mock(return_value=Response(200, json=_forecast()))
        async with httpx.AsyncClient() as http:
            fc = await weather.get_forecast(http, 31.3, 120.6)
    b = fc.basis()
    assert b.model == "best_match" and b.resolved_model == "ecmwf_ifs"
    assert b.issued_at == "2026-09-11T14:00:00+08:00"
    assert b.available_at.startswith("2026-09-11T21:09")
    weather.clear_cache()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta\.json").mock(return_value=Response(500))
        mock.get(url__regex=r".*/v1/forecast.*").mock(return_value=Response(200, json=_forecast()))
        async with httpx.AsyncClient() as http:
            fc = await weather.get_forecast(http, 31.3, 120.6)
    assert fc.basis().issued_at is None and fc.basis().fetched_at


async def test_显式模型按各自slug取元数据():
    from app.weather_model import current_model

    token = current_model.set("icon_global")
    try:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(url__regex=r".*/data/dwd_icon/static/meta\.json").mock(
                return_value=Response(200, json=META)
            )
            mock.get(url__regex=r".*/v1/forecast.*").mock(
                return_value=Response(200, json=_forecast())
            )
            async with httpx.AsyncClient() as http:
                fc = await weather.get_forecast(http, 31.3, 120.6)
        assert route.called and fc.basis().resolved_model == "dwd_icon"
    finally:
        current_model.reset(token)


async def test_自动选择复核不一致时退回无法确认(monkeypatch):
    saved = model_resolution.status()
    bump = {"best_match": 0.0}

    def answer(request: httpx.Request) -> Response:
        delta = bump.get(request.url.params["models"], 0.0)
        hourly = {f: [1.0 + delta, 2.0] for f in model_resolution.CHECK_FIELDS}
        return Response(200, json={"hourly": hourly})

    try:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=r".*/v1/forecast.*").mock(side_effect=answer)
            async with httpx.AsyncClient() as http:
                assert await model_resolution.check(http) is True
                assert model_resolution.resolve("best_match") == "ecmwf_ifs"
                bump["best_match"] = 0.5
                assert await model_resolution.check(http) is False
        assert model_resolution.resolve("best_match") is None
        assert model_resolution.resolve("gfs_global") == "ncep_gfs013"
    finally:
        model_resolution._state.update(saved)


# ────────────────────────────── 全目录 7 天 ──────────────────────────────


def _plant(id, capacity=1000, kind="wind"):
    from app.models import CatalogPlant

    return CatalogPlant(
        id=id,
        source="gem",
        source_id=id,
        provenance={"phases": [{"capacity_kw": capacity, "capacity_rating": "ac"}]},
        name=id,
        type=kind,
        capacity_kw=capacity,
        latitude=31.3,
        longitude=120.6,
        status="operating",
        province="江苏省",
    )


async def test_全目录未来7天_顶层字段等于首日(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    monkeypatch.setattr(fleet.tiles, "tile_dir", lambda: tmp_path / "tiles")
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta\.json").mock(return_value=Response(200, json=META))
        fc_raw = _forecast(wind_today=15)

        def batched(request):
            # 光伏与风电步长不同，一批里的坐标数不再恒为 1；响应条数必须对上
            n = len(request.url.params["latitude"].split(","))
            return Response(200, json=fc_raw if n == 1 else [fc_raw] * n)

        route = mock.get(url__regex=r".*/v1/forecast.*").mock(side_effect=batched)
        async with httpx.AsyncClient() as c:
            plants = [_plant("a"), _plant("b", 500, "solar")]
            await fleet.build(c, "gfs_global", fleet.day_key(), plants)
    out = fleet.load(tmp_path / f"gfs_global-{fleet.day_key()}.json")
    assert route.calls[0].request.url.params["forecast_days"] == "7"
    assert len(out["days"]) == 7 and [d["lead_days"] for d in out["days"]] == list(range(7))
    first = out["days"][0]
    assert first["date"] == out["date"] and first["energy_kwh"] == pytest.approx(out["energy_kwh"])
    assert first["solar_kwh"] == pytest.approx(out["solar_kwh"])
    assert first["regions"] == out["regions"]
    assert all(d["energy_kwh"] is not None and len(d["power_kw"]) == 24 for d in out["days"])
    assert out["basis"]["resolved_model"] == "ncep_gfs013" and out["basis"]["issued_at"]
    # 按目标日 + 签发日留档，供日后按预报时效评估
    leads = list((tmp_path / "fleet-history" / "leads" / "gfs_global").rglob("*.json"))
    assert len(leads) == 7


# ────────────────────────────── 逐日累积记限电损失 ──────────────────────────────


async def _session():
    from app.db import get_session

    gen = app.dependency_overrides[get_session]()
    return await gen.__anext__(), gen


async def test_累积表记录限电损失_可发电量不变(client: AsyncClient, open_meteo):
    d = await _create(client, {**WIND, "curtailment": {"mode": "ratio", "ratio_percent": 50}})
    await _create(client, {**SUZHOU})
    db, gen = await _session()
    try:
        await accumulate.accumulate_all(db, app.state.http)
        rows = {r.station_id: r for r in (await db.execute(select(DailyGeneration))).scalars()}
    finally:
        await gen.aclose()
    limited = rows[d["id"]]
    assert limited.curtailed_kwh == pytest.approx(limited.kwh * 0.5, abs=0.2)
    plain = next(r for sid, r in rows.items() if sid != d["id"])
    assert plain.curtailed_kwh is None
    body = (await client.get("/v1/stations")).json()["data"]
    mine = next(s for s in body["stations"] if s["id"] == d["id"])
    assert mine["metrics"]["grid_generation"] == pytest.approx(limited.kwh * 0.5, abs=0.3)
