"""全目录同目标日签发变化；只读留档，不触发气象请求。docs/20"""

import hashlib
import math
from datetime import date, datetime, timedelta

from app.errors import ApiError
from app.schemas.prediction import (
    FleetRegionSignal,
    FleetSignalHour,
    FleetSignalIssuance,
    FleetSignalModel,
    FleetSignalPair,
    FleetSignalRange,
    FleetSignalResponse,
    FleetSignalWindow,
    ForecastBasis,
)
from app.services import fleet_history
from app.weather_model import MODELS

UNKNOWN_REGION = "地区待补充"


def _local_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(fleet_history.TZ)


def _rows(model: str, target: str) -> list[dict]:
    folder = fleet_history.root() / "leads" / model / target
    if not folder.exists():
        return []
    rows = []
    # 一个目标日只有前七天的签发；保守设限防止坏目录拖慢请求。
    for path in sorted(folder.glob("*.json"), reverse=True)[:48]:
        row = fleet_history.read(path)
        if isinstance(row, dict) and row.get("date") == target and row.get("generated_at"):
            rows.append(row)
    return sorted(rows, key=lambda r: _local_time(r["generated_at"]), reverse=True)


def _issuance_key(row: dict) -> str:
    basis = row.get("basis") or {}
    # 起报缺失时按签发日期分组，不把 fetched_at 冒充起报。docs/17 §二
    issued_at = basis.get("issued_at")
    return (
        _local_time(issued_at).isoformat()
        if issued_at
        else _local_time(row["generated_at"]).date().isoformat()
    )


def _distinct(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for row in rows:
        key = _issuance_key(row)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _scope(row: dict, provinces: list[str]) -> dict | None:
    if not provinces:
        power = row.get("power_kw") or []
        curve = [p.get("value") for p in power if isinstance(p, dict)]
        if len(curve) != len(power):
            return None
        return {
            "solar": row.get("solar_kwh"),
            "wind": row.get("wind_kwh"),
            "energy": row.get("energy_kwh"),
            "capacity": row.get("covered_capacity_kw"),
            "curve": curve,
            "digest": row.get("coverage_digest"),
        }
    regions = row.get("region_series") or {}
    selected = [regions.get(name) for name in provinces]
    if any(r is None for r in selected):
        return None
    selected = [r for r in selected if r is not None]
    curves = [r.get("power_kw") or [] for r in selected]
    if not curves or len({len(c) for c in curves}) != 1:
        return None
    digests = [r.get("coverage_digest") for r in selected]
    digest = (
        hashlib.sha256(
            "\n".join(
                f"{name}:{part}" for name, part in zip(provinces, digests, strict=True)
            ).encode()
        ).hexdigest()
        if all(digests)
        else None
    )
    return {
        "solar": sum(r.get("solar_kwh") or 0 for r in selected),
        "wind": sum(r.get("wind_kwh") or 0 for r in selected),
        "energy": sum((r.get("solar_kwh") or 0) + (r.get("wind_kwh") or 0) for r in selected),
        "capacity": sum(r.get("covered_capacity_kw") or 0 for r in selected),
        "curve": [sum(values) for values in zip(*curves, strict=True)],
        "digest": digest,
    }


def _comparable(current: dict, previous: dict, a: dict | None, b: dict | None) -> bool:
    return bool(
        a
        and b
        and a.get("digest")
        and a["digest"] == b.get("digest")
        and current.get("calculation_version") == previous.get("calculation_version")
        and current.get("catalog_revision") == previous.get("catalog_revision")
        and current.get("calculation_version")
        and current.get("catalog_revision")
    )


def _pair(current: float | None, previous: float | None) -> FleetSignalPair | None:
    if (
        current is None
        or previous is None
        or not math.isfinite(current)
        or not math.isfinite(previous)
    ):
        return None
    return FleetSignalPair(
        current_kwh=round(current, 2),
        previous_kwh=round(previous, 2),
        change_percent=round((current - previous) / previous * 100, 1) if previous > 0 else None,
    )


def _hours(target: str, current: dict, previous: dict) -> list[FleetSignalHour]:
    a, b = current["curve"], previous["curve"]
    capacity = current["capacity"]
    if len(a) != 96 or len(b) != 96 or not capacity or capacity <= 0:
        return []
    result = []
    for h in range(24):
        now, before = a[h * 4 : h * 4 + 4], b[h * 4 : h * 4 + 4]
        if any(v is None or not math.isfinite(v) for v in [*now, *before]):
            continue
        current_kw, previous_kw = sum(now) / 4, sum(before) / 4
        result.append(
            FleetSignalHour(
                hour=f"{target}T{h:02d}:00:00+08:00",
                current_kw=round(current_kw, 2),
                previous_kw=round(previous_kw, 2),
                change_capacity_percent=round((current_kw - previous_kw) / capacity * 100, 2),
            )
        )
    return result


def _windows(hours: list[FleetSignalHour]) -> list[FleetSignalWindow]:
    top = sorted(
        (h for h in hours if abs(h.change_capacity_percent) >= 0.5),
        key=lambda h: abs(h.change_capacity_percent),
        reverse=True,
    )[:3]
    top.sort(key=lambda h: h.hour)
    groups: list[list[FleetSignalHour]] = []
    for item in top:
        if (
            groups
            and datetime.fromisoformat(item.hour) - datetime.fromisoformat(groups[-1][-1].hour)
            == timedelta(hours=1)
            and item.change_capacity_percent * groups[-1][-1].change_capacity_percent > 0
        ):
            groups[-1].append(item)
        else:
            groups.append([item])
    return [
        FleetSignalWindow(
            start_hour=group[0].hour,
            end_hour=(datetime.fromisoformat(group[-1].hour) + timedelta(hours=1)).isoformat(),
            change_capacity_percent=round(
                sum(x.change_capacity_percent for x in group) / len(group), 2
            ),
        )
        for group in groups
    ]


def _regions(current: dict, previous: dict) -> list[FleetRegionSignal]:
    a, b = current.get("region_series") or {}, previous.get("region_series") or {}
    result = []
    for name in a:
        if name == UNKNOWN_REGION or name not in b:
            continue
        after, before = _scope(current, [name]), _scope(previous, [name])
        if not _comparable(current, previous, after, before):
            continue
        result.append(
            FleetRegionSignal(
                province=name,
                solar=_pair(after["solar"], before["solar"]),
                wind=_pair(after["wind"], before["wind"]),
                combined=_pair(after["energy"], before["energy"]),
            )
        )
    return sorted(
        result,
        key=lambda r: abs(r.combined.change_percent or 0) if r.combined else 0,
        reverse=True,
    )


def _evolution(rows: list[dict], current: dict, provinces: list[str]) -> list[FleetSignalIssuance]:
    current_scope = _scope(current, provinces)
    result = []
    for row in rows:
        scope = _scope(row, provinces)
        if not _comparable(current, row, current_scope, scope):
            continue
        basis = row.get("basis") or {}
        result.append(
            FleetSignalIssuance(
                generated_at=row["generated_at"],
                issued_at=basis.get("issued_at"),
                solar_kwh=round(scope["solar"], 2),
                wind_kwh=round(scope["wind"], 2),
                energy_kwh=round(scope["energy"], 2),
            )
        )
        if len(result) == 7:
            break
    return list(reversed(result))


def _model_range(target: str, current: dict, provinces: list[str]) -> FleetSignalRange | None:
    baseline = _scope(current, provinces)
    members = []
    resolved: set[str] = set()
    # 自动选择可能实际落在 ECMWF 等已单列的模型；同一底层来源不能算两家分歧。
    for model in [current["model"], *sorted(MODELS - {current["model"]})]:
        latest = _distinct(_rows(model, target))
        if (
            not latest
            or _local_time(latest[0]["generated_at"]).date()
            != _local_time(current["generated_at"]).date()
        ):
            continue
        row = latest[0]
        scope = _scope(row, provinces)
        if not _comparable(current, row, baseline, scope) or scope["energy"] is None:
            continue
        actual = (row.get("basis") or {}).get("resolved_model") or model
        if actual in resolved:
            continue
        resolved.add(actual)
        members.append(
            FleetSignalModel(
                model=model, energy_kwh=round(scope["energy"], 2), generated_at=row["generated_at"]
            )
        )
    if len(members) < 2:
        return None
    values = sorted(m.energy_kwh for m in members)
    middle = values[len(values) // 2]
    return FleetSignalRange(
        members=members,
        low_kwh=values[0],
        high_kwh=values[-1],
        spread_percent=round((values[-1] - values[0]) / middle * 100, 1) if middle > 0 else None,
    )


def summary(model: str, target: date, provinces: list[str]) -> FleetSignalResponse:
    if model not in MODELS:
        raise ApiError("INVALID_PARAM", "不支持的气象模型", 400)
    today = datetime.now(fleet_history.TZ).date()
    if target < today or target > today + timedelta(days=6):
        raise ApiError("INVALID_PARAM", "仅支持今日起七天", 400)
    rows = _distinct(_rows(model, target.isoformat()))
    current = rows[0] if rows else None
    picked = list(dict.fromkeys(provinces))
    known = set(current.get("region_series") or {}) - {UNKNOWN_REGION} if current else set()
    missing_scope = bool(current and picked and any(name not in known for name in picked))
    result = FleetSignalResponse(
        date=target.isoformat(),
        provinces=picked,
        generated_at=current["generated_at"] if current else None,
        previous_generated_at=None,
        basis=ForecastBasis.model_validate(current["basis"])
        if current and current.get("basis")
        else None,
        solar=None,
        wind=None,
        combined=None,
        hours=[],
        top_windows=[],
        regions=[],
        evolution=[],
        model_range=None,
        reason="该日暂无签发预测" if not current else "等待下一轮可比预测",
    )
    if not current:
        return result
    if _local_time(current["generated_at"]).date() < today:
        result.reason = "沿用旧签发，等待今日更新"
        return result
    if missing_scope:
        result.reason = "所选地区暂无完整预测"
        return result
    result.evolution = _evolution(rows, current, picked)
    result.model_range = _model_range(target.isoformat(), current, picked)
    if len(rows) < 2:
        return result
    previous = rows[1]
    result.previous_generated_at = previous["generated_at"]
    result.regions = _regions(current, previous)
    after, before = _scope(current, picked), _scope(previous, picked)
    if not _comparable(current, previous, after, before):
        result.reason = "目录、口径或覆盖范围变化，暂不比较"
        return result
    result.solar = _pair(after["solar"], before["solar"])
    result.wind = _pair(after["wind"], before["wind"])
    result.combined = _pair(after["energy"], before["energy"])
    result.hours = _hours(target.isoformat(), after, before)
    result.top_windows = _windows(result.hours)
    result.reason = None
    return result
