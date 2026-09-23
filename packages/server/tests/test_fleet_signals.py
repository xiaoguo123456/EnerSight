"""风光变化只比较同口径、同覆盖场站的签发。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services import fleet_history, fleet_signals
from app.weather_model import supports_selection

TZ = ZoneInfo("Asia/Shanghai")


def test_变化接口沿用首页所选气象模型():
    assert supports_selection("/v1/predictions/fleet/signals")


def test_签发按北京时间归并且时区表达不影响去重():
    utc = {"generated_at": "2026-09-22T16:30:00+00:00", "basis": {"issued_at": None}}
    local = {"generated_at": "2026-09-23T00:30:00+08:00", "basis": {"issued_at": None}}
    assert fleet_signals._issuance_key(utc) == fleet_signals._issuance_key(local) == "2026-09-23"
    utc["basis"]["issued_at"] = "2026-09-22T16:00:00+00:00"
    local["basis"]["issued_at"] = "2026-09-23T00:00:00+08:00"
    assert fleet_signals._issuance_key(utc) == fleet_signals._issuance_key(local)


def _capture(
    monkeypatch,
    root,
    target: str,
    issued_day: date,
    solar: float,
    wind: float,
    ids: dict,
    model: str = "best_match",
    actual: str = "ecmwf_ifs",
):
    monkeypatch.setattr(fleet_history, "root", lambda: root)
    generated = datetime.combine(issued_day, datetime.min.time(), tzinfo=TZ).replace(hour=10)
    power = (solar + wind) / 24
    regions = {}
    for province, plant_ids in ids.items():
        share = len(plant_ids) / sum(len(v) for v in ids.values())
        regions[province] = {
            "days": {
                target: {
                    "power_kw": [power * share] * 96,
                    "solar_kwh": solar * share,
                    "wind_kwh": wind * share,
                    "covered_capacity_kw": 100 * len(plant_ids),
                    "coverage_digest": __import__("hashlib")
                    .sha256("\n".join(sorted(plant_ids)).encode())
                    .hexdigest(),
                }
            }
        }
    snapshot = {
        "model": model,
        "date": issued_day.isoformat(),
        "status": "ready",
        "generated_at": generated.isoformat(),
        "basis": {
            "model": model,
            "resolved_model": actual,
            "issued_at": generated.isoformat(),
            "available_at": generated.isoformat(),
            "fetched_at": generated.isoformat(),
        },
        "calculation_version": "v1",
        "catalog_revision": "same",
        "total_capacity_kw": 300,
        "days": [
            {
                "date": target,
                "energy_kwh": solar + wind,
                "solar_kwh": solar,
                "wind_kwh": wind,
                "covered_capacity_kw": 100 * sum(len(v) for v in ids.values()),
                "power_kw": [
                    {"time": f"{target}T{n // 4:02d}:{(n % 4) * 15:02d}:00+08:00", "value": power}
                    for n in range(96)
                ],
            }
        ],
    }
    coverage = {plant_id: [target] for values in ids.values() for plant_id in values}
    fleet_history.capture_leads(snapshot, {"regions": regions}, coverage)


def test_同批场站给变化与演变(monkeypatch, tmp_path):
    today = datetime.now(TZ).date()
    target = (today + timedelta(days=1)).isoformat()
    ids = {"江苏省": ["a"], "山东省": ["b"]}
    _capture(monkeypatch, tmp_path, target, today - timedelta(days=1), 100, 100, ids)
    _capture(monkeypatch, tmp_path, target, today, 120, 90, ids)

    result = fleet_signals.summary("best_match", date.fromisoformat(target), [])
    assert result.reason is None
    assert result.solar.change_percent == 20
    assert result.wind.change_percent == -10
    assert result.combined.change_percent == 5
    assert len(result.hours) == 24
    assert len(result.evolution) == 2
    assert {row.province for row in result.regions} == {"江苏省", "山东省"}


def test_全国覆盖变化不阻止可比省份上榜(monkeypatch, tmp_path):
    today = datetime.now(TZ).date()
    target = (today + timedelta(days=1)).isoformat()
    _capture(
        monkeypatch,
        tmp_path,
        target,
        today - timedelta(days=1),
        100,
        100,
        {"江苏省": ["a"], "山东省": ["b"]},
    )
    _capture(monkeypatch, tmp_path, target, today, 120, 90, {"江苏省": ["a"], "山东省": ["b", "c"]})

    national = fleet_signals.summary("best_match", date.fromisoformat(target), [])
    assert national.combined is None
    assert national.reason == "目录、口径或覆盖范围变化，暂不比较"
    assert [row.province for row in national.regions] == ["江苏省"]

    jiangsu = fleet_signals.summary("best_match", date.fromisoformat(target), ["江苏省"])
    assert jiangsu.combined is not None
    assert jiangsu.reason is None


def test_同一次起报重算不制造变化(monkeypatch, tmp_path):
    today = datetime.now(TZ).date()
    target = (today + timedelta(days=1)).isoformat()
    ids = {"江苏省": ["a"]}
    _capture(monkeypatch, tmp_path, target, today, 100, 100, ids)
    _capture(monkeypatch, tmp_path, target, today, 120, 90, ids)
    result = fleet_signals.summary("best_match", date.fromisoformat(target), [])
    assert result.combined is None
    assert result.reason == "等待下一轮可比预测"


def test_跨日沿用旧签发不展示旧变化和关注提醒(monkeypatch, tmp_path):
    today = datetime.now(TZ).date()
    target = (today + timedelta(days=1)).isoformat()
    ids = {"江苏省": ["a"]}
    _capture(monkeypatch, tmp_path, target, today - timedelta(days=2), 100, 100, ids)
    _capture(monkeypatch, tmp_path, target, today - timedelta(days=1), 120, 90, ids)

    result = fleet_signals.summary("best_match", date.fromisoformat(target), [])
    assert result.reason == "沿用旧签发，等待今日更新"
    assert result.generated_at is not None
    assert result.combined is None
    assert result.regions == []
    assert result.evolution == []


def test_多省筛选缺省份时保留原范围并不给部分合计(monkeypatch, tmp_path):
    today = datetime.now(TZ).date()
    target = (today + timedelta(days=1)).isoformat()
    _capture(
        monkeypatch,
        tmp_path,
        target,
        today - timedelta(days=1),
        100,
        100,
        {"江苏省": ["a"], "山东省": ["b"]},
    )
    _capture(monkeypatch, tmp_path, target, today, 120, 90, {"江苏省": ["a"]})

    result = fleet_signals.summary(
        "best_match", date.fromisoformat(target), ["江苏省", "山东省"]
    )
    assert result.provinces == ["江苏省", "山东省"]
    assert result.reason == "所选地区暂无完整预测"
    assert result.combined is None
    assert result.regions == []


def test_模型分歧按实际底层来源去重(monkeypatch, tmp_path):
    today = datetime.now(TZ).date()
    target = (today + timedelta(days=1)).isoformat()
    ids = {"江苏省": ["a"]}
    _capture(monkeypatch, tmp_path, target, today, 100, 100, ids)
    _capture(monkeypatch, tmp_path, target, today, 120, 100, ids, "ecmwf_ifs")
    _capture(monkeypatch, tmp_path, target, today, 110, 110, ids, "gfs_global", "ncep_gfs013")

    result = fleet_signals.summary("best_match", date.fromisoformat(target), [])
    assert result.model_range is not None
    assert {row.model for row in result.model_range.members} == {"best_match", "gfs_global"}
