"""限电第二层：省级月度利用率参考。不改可发电量与指数，只给公开电站与全目录。docs/17 §四"""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.services import fleet_prediction as fleet
from app.services import prediction, province_grid, weather
from app.services.station import from_catalog
from tests.test_prediction import batched, forecast, plant, station


@pytest.fixture
def rates(tmp_path, monkeypatch):
    def write(periods):
        path = tmp_path / "province_utilization.json"
        path.write_text(
            json.dumps({"source": "测试来源", "periods": periods}, ensure_ascii=False),
            encoding="utf-8",
        )
        monkeypatch.setattr(province_grid, "DATA_FILE", path)
        province_grid.reset()

    yield write
    province_grid.reset()


def today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def test_内蒙古按盟市分蒙东蒙西_城市不详不猜():
    assert province_grid.region_of("内蒙古自治区", "Chifeng") == "蒙东"
    assert province_grid.region_of("内蒙古自治区", "Xilingol League") == "蒙西"
    assert province_grid.region_of("内蒙古自治区", None) is None
    assert province_grid.region_of("江苏省", "Yancheng") == "江苏"
    assert province_grid.region_of(None, None) is None


def test_取目标月及以前最近一期当月值_没有再退全年(rates):
    rates(
        [
            {"period": "2025", "kind": "year", "rates": {"甘肃": {"wind": 0.95, "solar": 0.93}}},
            {"period": "2026-02", "kind": "month", "rates": {"甘肃": {"wind": 0.9}}},
            {"period": "2026-03", "kind": "month", "rates": {"甘肃": {"wind": 0.85}}},
        ]
    )
    lookup = province_grid.lookup
    april = lookup("甘肃", "wind", date(2026, 4, 10))
    assert (april.period, april.value, april.source) == ("2026-03", 0.85, "测试来源")
    assert lookup("甘肃", "wind", date(2026, 2, 15)).period == "2026-02"
    assert lookup("甘肃", "wind", date(2026, 1, 5)).period == "2025"
    assert lookup("甘肃", "solar", date(2026, 4, 10)).period == "2025"  # 月度缺光伏，退全年
    assert lookup("甘肃", "wind", date(2024, 6, 1)) is None
    assert lookup("青海", "wind", date(2026, 4, 10)) is None


def test_没有当年当月时优先往年同月而不是最近的冬季月份(rates):
    rates(
        [
            {"period": "2025-09", "kind": "month", "rates": {"甘肃": {"wind": 0.97}}},
            {"period": "2025-12", "kind": "month", "rates": {"甘肃": {"wind": 0.93}}},
            {"period": "2026-01", "kind": "month", "rates": {"甘肃": {"wind": 0.88}}},
        ]
    )
    assert province_grid.lookup("甘肃", "wind", date(2026, 9, 15)).period == "2025-09"
    assert province_grid.lookup("甘肃", "wind", date(2026, 1, 20)).period == "2026-01"
    assert province_grid.lookup("甘肃", "wind", date(2026, 2, 1)).period == "2026-01"


def test_公开电站按省级利用率折算_自建场站与无数据为空(rates):
    rates([{"period": f"{today():%Y-%m}", "kind": "month", "rates": {"江苏": {"wind": 0.9}}}])
    fc = weather.parse_forecast(forecast())
    public = prediction.compute(from_catalog(plant("a")), fc)
    g = public.province_grid
    assert (g.region, g.period, g.utilization) == ("江苏", f"{today():%Y-%m}", 0.9)
    assert g.energy_kwh == pytest.approx(public.energy_kwh * 0.9, abs=0.02)
    assert g.energy_kwh + g.curtailed_kwh == pytest.approx(public.energy_kwh, abs=0.02)
    assert public.grid_energy_kwh is None  # 不占用第一层的上网口径
    assert prediction.compute(station(), fc).province_grid is None
    rates([])
    assert prediction.compute(from_catalog(plant("a")), fc).province_grid is None


async def test_全目录逐日按省折算并计数(rates, tmp_path, monkeypatch):
    months = sorted({f"{today() + timedelta(days=k):%Y-%m}" for k in range(8)})
    rates(
        [
            {"period": m, "kind": "month", "rates": {"江苏": {"wind": 0.8, "solar": 0.9}}}
            for m in months
        ]
    )
    monkeypatch.setattr(fleet, "directory", lambda: tmp_path)
    unknown = plant("nm")
    unknown.province = "内蒙古自治区"  # 城市不详，分不出蒙东蒙西，不折算
    plants = [plant("a"), plant("b", capacity=2000, kind="solar"), unknown]
    with respx.mock:
        respx.get(url__regex=r".*/static/meta\.json").mock(return_value=httpx.Response(404))
        respx.get(url__regex=r".*open-meteo.*/v1/forecast.*").mock(side_effect=batched(forecast()))
        async with httpx.AsyncClient() as c:
            await fleet.build(c, "gfs_global", fleet.day_key(), plants)
    out = fleet.load(tmp_path / f"gfs_global-{fleet.day_key()}.json")
    day = out["days"][0]
    g = day["province_grid"]
    assert (g["applied_count"], g["unapplied_count"]) == (2, 1)
    assert g["energy_kwh"] + g["curtailed_kwh"] == pytest.approx(day["energy_kwh"], rel=1e-6)
    assert 0 < g["curtailed_kwh"] < day["energy_kwh"] * 0.2
    assert g["periods"] == [f"{today():%Y-%m}"]
