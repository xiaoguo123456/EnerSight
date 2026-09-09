"""容量口径与留档必须可追溯，不能依靠模型名称猜测原始容量。"""

import json

from app.models import CatalogPlant
from app.services import prediction, prediction_archive, weather
from app.services.prediction_basis import catalog_basis
from tests.test_prediction import forecast, station


def test_交直流分别转换并拒绝未知类型():
    p = CatalogPlant(
        type="solar",
        provenance={
            "phases": [
                {"capacity_kw": 1000, "capacity_rating": "ac", "technology": "PV"},
                {"capacity_kw": 1200, "capacity_rating": "dc"},
            ]
        },
    )
    assert catalog_basis(p) == ((2400, 2000), None)
    p.provenance = {"phases": [{"capacity_kw": 1000, "capacity_rating": "unknown"}]}
    assert catalog_basis(p)[0] is None
    p.provenance = None
    assert catalog_basis(p)[0] is None


def test_待核验不能生成貌似有效的电量():
    s = station("solar")
    s._prediction_blocked = "容量类型未知"
    p = prediction.compute(s, weather.parse_forecast(forecast()))
    assert p.energy_kwh is None
    assert all(v.value is None for v in p.power_kw)
    assert "容量类型未知" in p.assumptions


def test_分期交流上限约束():
    s = station("solar")
    s._pv_capacity = (1500, 800)
    p = prediction.compute(s, weather.parse_forecast(forecast()))
    assert p.energy_kwh > 0
    assert max(v.value for v in p.power_kw) <= 800


def test_留档不覆盖且区分今日明日(tmp_path, monkeypatch):
    monkeypatch.setattr(prediction_archive.tiles, "tile_dir", lambda: tmp_path / "tiles")
    s = station()
    s.id = "gem:test"
    fc = weather.parse_forecast(forecast())
    today = prediction.compute(s, fc)
    tomorrow = prediction.compute(s, fc, day_offset=1)
    prediction_archive.save(s, fc, today)
    prediction_archive.save(s, fc, tomorrow)
    paths = list((tmp_path / "prediction-archive").rglob("*.json"))
    before = {p: p.read_text() for p in paths}
    today.energy_kwh = 1
    prediction_archive.save(s, fc, today)
    assert len(paths) == 2
    assert all(p.read_text() == old for p, old in before.items())
    assert {json.loads(p.read_text())["target_date"] for p in paths} == {today.date, tomorrow.date}
