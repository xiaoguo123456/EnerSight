"""按地区筛选：逐省明细做加法，覆盖统计的分母跟着换。docs/17 §二"""

from datetime import date, timedelta

import httpx
import pytest
import respx

from app.errors import ApiError
from app.services import fleet_prediction as fleet
from tests.test_prediction import forecast, plant


def located(id, province, city=None, **kwargs):
    p = plant(id, **kwargs)
    p.province = province
    p.city = city
    return p


async def built(tmp_path, monkeypatch, plants):
    """跑一轮真实的全目录计算，拿到快照与逐省明细。"""
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta\.json").respond(404)
        mock.get(url__regex=r".*/v1/forecast.*").respond(200, json=forecast())
        async with httpx.AsyncClient() as http:
            await fleet.build(http, "gfs_global", fleet.day_key(), plants)
    day = fleet.day_key()
    snapshot = fleet.FleetPrediction.model_validate(fleet.load(tmp_path / f"gfs_global-{day}.json"))
    detail = fleet.load_regions("gfs_global", day)
    assert detail is not None
    return snapshot, detail


@pytest.fixture
async def two_provinces(tmp_path, monkeypatch):
    plants = [
        located("a", "江苏省", "南京市"),
        located("b", "江苏省", "苏州市", capacity=2000),
        located("c", "云南省", "昆明市", capacity=500),
    ]
    return await built(tmp_path, monkeypatch, plants)


async def test_分省相加等于全目录(two_provinces):
    snapshot, detail = two_provinces
    both = fleet.build_scoped(snapshot, detail, ["江苏省", "云南省"])
    one = fleet.build_scoped(snapshot, detail, ["江苏省"])
    other = fleet.build_scoped(snapshot, detail, ["云南省"])
    assert snapshot.covered_count == 3 and one.covered_count == 2 and other.covered_count == 1
    for k in range(len(snapshot.days)):
        national, a, b = snapshot.days[k], one.days[k], other.days[k]
        assert a.energy_kwh + b.energy_kwh == pytest.approx(national.energy_kwh)
        assert both.days[k].energy_kwh == pytest.approx(national.energy_kwh)
        assert a.wind_kwh + b.wind_kwh == pytest.approx(national.wind_kwh)
        # 曲线逐点相加，时刻标签不变
        for point, left, right in zip(national.power_kw, a.power_kw, b.power_kw, strict=True):
            assert point.time == left.time == right.time
            assert left.value + right.value == pytest.approx(point.value)
    assert both.energy_kwh == pytest.approx(snapshot.energy_kwh)
    assert both.power_kw[0].value == pytest.approx(snapshot.power_kw[0].value)


async def test_覆盖统计的分母按省重算(two_provinces):
    """拿全国容量当分母会把一个省显示成覆盖百分之零点几。"""
    snapshot, detail = two_provinces
    one = fleet.build_scoped(snapshot, detail, ["云南省"])
    assert snapshot.total_capacity_kw == 3500 and snapshot.total_count == 3
    assert one.total_capacity_kw == 500 and one.total_count == 1
    assert one.eligible_count == 1 and one.covered_capacity_kw == 500
    assert one.failed_count == 0 and one.status == "ready"


async def test_未覆盖的省仍留分母_不当成没有这个省(tmp_path, monkeypatch):
    plants = [located("a", "江苏省"), located("bad", "云南省", capacity=-1)]
    snapshot, detail = await built(tmp_path, monkeypatch, plants)
    assert "云南省" in detail["regions"]
    one = fleet.build_scoped(snapshot, detail, ["云南省"])
    assert one.total_count == 1 and one.invalid_count == 1 and one.eligible_count == 0
    assert one.covered_count == 0 and one.energy_kwh is None and one.status == "error"


async def test_逐省限电参考相加_统计期取并集(two_provinces):
    snapshot, detail = two_provinces
    both = fleet.build_scoped(snapshot, detail, ["江苏省", "云南省"])
    national, merged = snapshot.days[0].province_grid, both.days[0].province_grid
    assert merged is not None and national is not None
    assert merged.energy_kwh == pytest.approx(national.energy_kwh, abs=0.05)
    assert merged.applied_count == national.applied_count
    assert set(merged.periods) == set(national.periods)


async def test_未知省份忽略_全部无效报错(two_provinces, monkeypatch):
    snapshot, detail = two_provinces
    monkeypatch.setattr(fleet, "ensure", lambda *a: _ready(snapshot))
    monkeypatch.setattr(fleet, "load_regions", lambda *a: detail)
    kept = await fleet.ensure_scoped(None, "gfs_global", ["江苏省", "火星省"])
    assert [r.province for r in kept.days[0].regions] == ["江苏省"]
    with pytest.raises(ApiError) as bad:
        await fleet.ensure_scoped(None, "gfs_global", ["火星省"])
    assert bad.value.code == "INVALID_PARAM"


async def test_地区待补充不作为筛选项(tmp_path, monkeypatch):
    plants = [located("a", "江苏省"), located("x", None)]
    snapshot, detail = await built(tmp_path, monkeypatch, plants)
    assert fleet.UNKNOWN_REGION in detail["regions"]
    assert fleet.UNKNOWN_REGION not in fleet.selectable_regions(detail)
    monkeypatch.setattr(fleet, "ensure", lambda *a: _ready(snapshot))
    monkeypatch.setattr(fleet, "load_regions", lambda *a: detail)
    with pytest.raises(ApiError):
        await fleet.ensure_scoped(None, "gfs_global", [fleet.UNKNOWN_REGION])


async def test_明细缺失时按准备中返回而不是猜数字(two_provinces, monkeypatch):
    snapshot, _ = two_provinces
    monkeypatch.setattr(fleet, "ensure", lambda *a: _ready(snapshot))
    monkeypatch.setattr(fleet, "load_regions", lambda *a: None)
    out = await fleet.ensure_scoped(None, "gfs_global", ["江苏省"])
    assert out.energy_kwh is None and out.days == [] and out.power_kw == []
    assert "准备" in out.message


def test_跨日沿用昨日明细按日期对齐(tmp_path, monkeypatch):
    """明细以日期为键：按下标取会把昨天的数字当成今天的。"""
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    today = fleet.day_key()
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    from tests.test_fleet_snapshots import snapshot as fake

    fleet.write(tmp_path / f"gfs_global-{yesterday}.json", fake(yesterday))
    days = {
        (date.fromisoformat(yesterday) + timedelta(days=k)).isoformat(): {
            "power_kw": [float(k)] * 96,
            "solar_kwh": 0.0,
            "wind_kwh": float(k) * 24,
            "covered_count": 1,
            "covered_capacity_kw": 1000.0,
            "common_energy_kwh": None,
            "province_grid": None,
        }
        for k in range(7)
    }
    fleet.write(
        fleet.regions_path("gfs_global", yesterday),
        {
            "day": yesterday,
            "resolution_minutes": 15,
            "regions": {
                "江苏省": {
                    "total_count": 1,
                    "total_capacity_kw": 1000.0,
                    "eligible_count": 1,
                    "duplicate_count": 0,
                    "invalid_count": 0,
                    "common_covered_count": 0,
                    "common_capacity_kw": 0.0,
                    "days": days,
                }
            },
        },
    )
    carried = fleet.carry_previous("gfs_global", today)
    assert carried is not None
    detail = fleet.load_regions("gfs_global", today)
    assert detail["day"] == today
    out = fleet.build_scoped(fleet.FleetPrediction.model_validate(carried), detail, ["江苏省"])
    # 今日是昨日明细里的第二天：96 × 1 kW × 0.25 h
    assert out.days[0].date == today and out.energy_kwh == pytest.approx(24)
    # 昨日明细只到第七天，今日的第七天没有数据，不补造
    assert out.days[-1].energy_kwh is None


def test_逐省分母把重复与不合格算在所在省():
    plants = [
        located("a", "江苏省"),
        located("a", "江苏省"),
        located("bad", "云南省", capacity=0),
    ]
    rows, dropped = fleet.eligible(plants)
    verified = [p for p in rows if not fleet.catalog_basis(p)[1]]
    totals = fleet.region_totals(plants, rows, verified, dropped)
    assert totals["江苏省"]["duplicate_count"] == 1 and totals["江苏省"]["total_count"] == 1
    assert totals["云南省"]["invalid_count"] == 1 and totals["云南省"]["total_capacity_kw"] == 0


async def test_接口把逗号分隔的省份传下去(client, monkeypatch, two_provinces):
    snapshot, detail = two_provinces
    monkeypatch.setattr(fleet, "ensure", lambda *a: _ready(snapshot))
    monkeypatch.setattr(fleet, "load_regions", lambda *a: detail)
    response = await client.get("/v1/predictions/fleet", params={"provinces": "云南省, 江苏省"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert {r["province"] for r in data["regions"]} == {"云南省", "江苏省"}
    assert "本页仅统计所选 2 个地区" in "\n".join(data["assumptions"])
    bad = await client.get("/v1/predictions/fleet", params={"provinces": "火星省"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_PARAM"
    whole = await client.get("/v1/predictions/fleet")
    assert whole.status_code == 200 and whole.json()["data"]["covered_count"] == 3


async def _ready(snapshot):
    return snapshot
