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


def regions(split=True):
    rows = [
        dict(province="江苏省", energy_kwh=60, covered_count=5),
        dict(province="云南省", energy_kwh=40, covered_count=3),
    ]
    if split:
        for r, solar in zip(rows, (36, 10), strict=True):
            r.update(solar_kwh=solar, wind_kwh=r["energy_kwh"] - solar, covered_capacity_kw=50)
    return rows


def test_分省历史按省相加_缺拆分只给总量(monkeypatch, tmp_path):
    at = setup(monkeypatch, tmp_path)
    today = history.capture(
        {**snapshot(day="2026-09-10"), "regions": regions()}, "v1", at("2026-09-10T11:00")
    )
    # 上线前的留档没有光伏/风电拆分，那天只报总量
    history.capture(
        {**snapshot(day="2026-09-09"), "regions": regions(split=False)},
        "v1",
        at("2026-09-09T11:00"),
    )
    assert today is None
    out = history.summary(
        "best_match", "week", date(2026, 9, 10), at("2026-09-10T13:00"), ["云南省"]
    )
    assert out["provinces"] == ["云南省"]
    # 两天都有云南 40，但 9-09 没拆分 → 总量可加、拆分整体为 null
    assert out["energy_kwh"] == 80 and out["solar_kwh"] is None
    day10 = next(d for d in out["days"] if d["date"] == "2026-09-10")
    assert day10["record"]["energy_kwh"] == 40 and day10["record"]["solar_kwh"] == 10
    both = history.summary(
        "best_match", "week", date(2026, 9, 10), at("2026-09-10T13:00"), ["云南省", "江苏省"]
    )
    assert both["energy_kwh"] == 200 == out["energy_kwh"] + 120


def test_没留分省明细或该省未覆盖的日子算无记录(monkeypatch, tmp_path):
    """0 电量和「没数据」是两回事；少一个省的和也不是这几个省的和。"""
    at = setup(monkeypatch, tmp_path)
    history.capture(snapshot(day="2026-09-10"), "v1", at("2026-09-10T11:00"))
    out = history.summary(
        "best_match", "week", date(2026, 9, 10), at("2026-09-10T13:00"), ["云南省"]
    )
    assert out["recorded_days"] == 0
    assert next(d for d in out["days"] if d["date"] == "2026-09-10")["state"] == "missing"
    # 不带 provinces 时仍是全国口径，照常有记录
    national = history.summary("best_match", "week", date(2026, 9, 10), at("2026-09-10T13:00"))
    assert national["recorded_days"] == 1 and national["energy_kwh"] == 100
    # 有明细但所选省当天没被覆盖：同样按无记录，不报成 0
    history.capture(
        {**snapshot(day="2026-09-11"), "regions": regions()}, "v1", at("2026-09-11T11:00")
    )
    gap = history.summary(
        "best_match", "week", date(2026, 9, 11), at("2026-09-11T13:00"), ["云南省", "西藏自治区"]
    )
    assert gap["recorded_days"] == 0


def test_从版本留档回填分省明细_不动已封存的数值(monkeypatch, tmp_path):
    at = setup(monkeypatch, tmp_path)
    history.capture(snapshot(day="2026-09-09"), "v1", at("2026-09-09T11:00"))
    path = history.root() / "best_match" / "2026-09-09.json"
    row = history.read(path)
    assert row["regions"] == []
    # 主记录当初只存了全国合计，但版本留档里是完整快照
    history.write(path, {k: v for k, v in row.items() if k != "regions"} | {"sealed": True})
    history.write(
        history.root() / "versions" / "v1" / "best_match" / "2026-09-09.json",
        {**snapshot(day="2026-09-09"), "regions": regions(), "version": "v1"},
    )
    assert history.backfill_regions() == 1
    filled = history.read(path)
    assert [r["province"] for r in filled["regions"]] == ["江苏省", "云南省"]
    assert filled["energy_kwh"] == 100 and filled["sealed"] is True
    assert history.backfill_regions() == 0
