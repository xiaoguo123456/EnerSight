from datetime import date, datetime

from app.services import fleet_history as history


def snapshot(day="2026-09-09", energy=100, hour=10, model="best_match"):
    return dict(
        model=model,
        date=day,
        generated_at=f"{day}T{hour:02d}:00:00+08:00",
        status="ready",
        energy_kwh=energy,
        solar_kwh=energy * 0.6,
        wind_kwh=energy * 0.4,
        covered_count=8,
        total_count=10,
        covered_capacity_kw=80,
        total_capacity_kw=100,
    )


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(history.tiles, "tile_dir", lambda: tmp_path / "tiles")
    return lambda d: datetime.fromisoformat(d).replace(tzinfo=history.TZ)


def test_同日只累计一次_模型分开_过期不补算(monkeypatch, tmp_path):
    at = setup(monkeypatch, tmp_path)
    history.capture(snapshot(), "v1", at("2026-09-09T11:00"))
    history.capture(snapshot(energy=200, hour=12), "v2", at("2026-09-09T13:00"))
    history.capture(snapshot(model="ecmwf_ifs", energy=30), "v2", at("2026-09-09T13:00"))
    late = snapshot(day="2026-09-08")
    late["generated_at"] = "2026-09-09T10:00:00+08:00"
    history.capture(late, "v2", at("2026-09-09T13:00"))
    out = history.summary("best_match", "week", date(2026, 9, 9), at("2026-09-09T13:00"))
    assert out["energy_kwh"] == 100 and out["recorded_days"] == 1
    assert out["versions"] == ["v1"]
    separate = history.read(history.root() / "versions" / "v2" / "best_match" / "2026-09-09.json")
    assert separate["energy_kwh"] == 200
    assert out["days"][0]["state"] == "missing"
    assert out["days"][-1]["state"] == "future"
    assert out["solar_kwh"] + out["wind_kwh"] == out["energy_kwh"]


def test_封存不覆盖_真实零值不同于缺测(monkeypatch, tmp_path):
    at = setup(monkeypatch, tmp_path)
    history.capture(snapshot(energy=0), "v1", at("2026-09-09T12:00"))
    history.checkpoint(at("2026-09-10T08:15"))
    history.capture(snapshot(energy=400, hour=23), "v2", at("2026-09-10T09:00"))
    out = history.summary("best_match", "month", date(2026, 9, 9), at("2026-09-10T09:00"))
    assert out["energy_kwh"] == 0 and out["provisional_days"] == 0
    assert out["buckets"][8]["energy_kwh"] == 0
    assert out["buckets"][7]["energy_kwh"] is None


def test_年汇总和闰月(monkeypatch, tmp_path):
    at = setup(monkeypatch, tmp_path)
    for d in ["2024-02-28", "2024-02-29", "2024-03-01"]:
        history.capture(snapshot(day=d), "v1", at("2024-03-02T10:00"))
    out = history.summary("best_match", "year", date(2024, 3, 2), at("2024-03-02T10:00"))
    assert out["energy_kwh"] == 300 and len(out["buckets"]) == 12
    assert out["buckets"][1]["energy_kwh"] == 200
    month = history.summary("best_match", "month", date(2024, 2, 1), at("2024-03-02T10:00"))
    assert len(month["days"]) == 29
