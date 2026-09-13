"""全目录日快照持久留存。只接收已经生成的结果，不追算缺失日期。"""

import calendar
import hashlib
import json
import math
import threading
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from app.render import tiles
from app.weather_model import MODELS

TZ = ZoneInfo("Asia/Shanghai")
_LOCK = threading.RLock()


def locked(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)

    return wrapped


def root() -> Path:
    p = tiles.tile_dir().parent / "fleet-history"
    p.mkdir(parents=True, exist_ok=True)
    return p


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
    tmp.replace(path)


@locked
def capture(snapshot, version, now=None):
    now = now or datetime.now(TZ)
    if snapshot.get("model") not in MODELS or snapshot.get("status") not in ("ready", "partial"):
        return
    energy = snapshot.get("energy_kwh")
    if energy is None or not math.isfinite(energy):
        return
    target = date.fromisoformat(snapshot["date"])
    issued = datetime.fromisoformat(snapshot["generated_at"]).astimezone(TZ)
    # 只归档当日已经生成的快照；拒绝把今天追算的昨天结果写入历史。
    if issued.date() != target or target > now.date():
        return
    path = root() / snapshot["model"] / f"{target}.json"
    old = read(path)
    if old and old.get("sealed"):
        return
    # 各计算版本另存一份，既有主记录不会被新口径替换。
    version_path = root() / "versions" / version / snapshot["model"] / f"{target}.json"
    previous = read(version_path)
    if previous is None or datetime.fromisoformat(previous["generated_at"]) < issued:
        write(version_path, {**snapshot, "version": version})
    old = read(path)
    if old and old.get("version") != version:
        return
    if old and (old.get("sealed") or datetime.fromisoformat(old["generated_at"]) >= issued):
        return
    row = {
        k: snapshot[k]
        for k in (
            "date",
            "model",
            "generated_at",
            "energy_kwh",
            "solar_kwh",
            "wind_kwh",
            "covered_count",
            "total_count",
            "covered_capacity_kw",
            "total_capacity_kw",
        )
    }
    row.update(
        version=version,
        versions=sorted(set((old or {}).get("versions", []) + [version])),
        sealed=False,
    )
    write(path, row)


@locked
def capture_leads(snapshot):
    """每次签发保留完整逐日曲线与来源，不覆盖早先签发结果。"""
    if snapshot.get("model") not in MODELS or snapshot.get("status") not in ("ready", "partial"):
        return
    issued = datetime.fromisoformat(snapshot["generated_at"]).astimezone(TZ)
    for d in snapshot.get("days") or []:
        if d.get("energy_kwh") is None:
            continue
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:16]
        path = root() / "leads" / snapshot["model"] / d["date"] / f"{issued.date()}-{digest}.json"
        row = dict(d)
        row.update(
            model=snapshot["model"],
            generated_at=snapshot["generated_at"],
            basis=snapshot.get("basis"),
            calculation_version=snapshot.get("calculation_version"),
            input_archive_id=snapshot.get("input_archive_id"),
            catalog_revision=snapshot.get("catalog_revision"),
            total_capacity_kw=snapshot.get("total_capacity_kw"),
            common_covered_count=snapshot.get("common_covered_count"),
        )
        if not path.exists():
            write(path, row)


@locked
def checkpoint(now=None):
    now = now or datetime.now(TZ)
    # 接管尚未清理的既有缓存；不请求任何历史气象数据。
    source = tiles.tile_dir().parent / "fleet-predictions"
    candidates = []
    for path in source.rglob("*.json"):
        if path.name.endswith("-weather.json"):
            continue
        value = read(path)
        if value and value.get("model") in MODELS:
            version = value.get("calculation_version") or (
                path.parent.name if path.parent != source else "capacity-v1"
            )
            candidates.append((value, version))
    for value, version in sorted(candidates, key=lambda item: item[0].get("generated_at", "")):
        capture(value, version, now)
    for path in root().glob("*/*.json"):
        row = read(path)
        if row and not row.get("sealed"):
            target = date.fromisoformat(row["date"])
            deadline = datetime.combine(
                target + timedelta(days=1), datetime.min.time(), TZ
            ) + timedelta(hours=8, minutes=15)
            if now >= deadline:
                row["sealed"] = True
                row["sealed_at"] = now.isoformat()
                write(path, row)


def summary(model, period, anchor, now=None):
    now = now or datetime.now(TZ)
    checkpoint(now)
    if period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
    elif period == "month":
        start = anchor.replace(day=1)
        end = anchor.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
    else:
        start = date(anchor.year, 1, 1)
        end = date(anchor.year, 12, 31)
    all_rows = [r for path in (root() / model).glob("*.json") if (r := read(path))]
    by_date = {r["date"]: r for r in all_rows}
    days = []
    for i in range((end - start).days + 1):
        d = start + timedelta(days=i)
        r = by_date.get(str(d))
        days.append(
            dict(
                date=str(d),
                state="future"
                if d > now.date()
                else "sealed"
                if r and r["sealed"]
                else "provisional"
                if r
                else "missing",
                record=r,
            )
        )
    records = [d["record"] for d in days if d["record"]]

    def aggregate(rs):
        return dict(
            energy_kwh=sum(r["energy_kwh"] for r in rs) if rs else None,
            solar_kwh=sum(r["solar_kwh"] for r in rs) if rs else None,
            wind_kwh=sum(r["wind_kwh"] for r in rs) if rs else None,
            recorded_days=len(rs),
            provisional_days=sum(not r["sealed"] for r in rs),
        )

    buckets = []
    if period == "year":
        for m in range(1, 13):
            rs = [r for r in records if date.fromisoformat(r["date"]).month == m]
            buckets.append(dict(key=f"{anchor.year}-{m:02d}-01", label=f"{m}月", **aggregate(rs)))
    else:
        for d in days:
            buckets.append(
                dict(
                    key=d["date"],
                    label=d["date"][5:],
                    **aggregate([d["record"]] if d["record"] else []),
                )
            )
    coverage = [
        r["covered_capacity_kw"] / r["total_capacity_kw"] * 100
        for r in records
        if r["total_capacity_kw"] > 0
    ]
    return dict(
        model=model,
        period=period,
        start=str(start),
        end=str(end),
        today=str(now.date()),
        first_recorded=min(by_date) if by_date else None,
        **aggregate(records),
        expected_days=max(0, (min(end, now.date()) - start).days + 1),
        coverage_min=min(coverage) if coverage else None,
        coverage_max=max(coverage) if coverage else None,
        versions=sorted({v for r in records for v in r["versions"]}),
        capacity_changed=len(
            {(r["total_count"], r["total_capacity_kw"], r["covered_capacity_kw"]) for r in records}
        )
        > 1,
        buckets=buckets,
        days=days,
    )
