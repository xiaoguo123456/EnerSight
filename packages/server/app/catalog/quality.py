"""公开电站目录的数据质量处理。docs/04 §七

GEM 大量场站只有省、市级占位坐标（一个省几十座叠在同一点），WRI 2021 版中国部分几乎都已被
GEM 覆盖，GEM 同址多期合并后的名称以英文区县开头、彼此撞名。导入后统一处理：

1. 占位坐标：同一坐标 ≥ 3 座且跨市（省级占位）或跨县（市级占位）时，改用同县坐标可靠场站的
   中位点；省级占位没有同县参照时退到同市。原坐标记入 provenance.location。找不到参照的
   省级占位不参与发电预测（prediction_basis.catalog_basis）
2. WRI 重复：20 km 内同类型 GEM 运行容量合计不小于它，或 500 MW 以上的汇总条目在 50 km 内
   被覆盖，状态记为 duplicate，不再出现在列表、地图与汇总里
3. 其余 WRI 没有省份，取 50 km 内最近 GEM 场站的省、市
4. 多期合并场址用最大一期的中文名「等 N 期」；同类型重名的按英文名里的期号或容量加后缀

只依赖库内字段，重复执行结果不变。每次导入之后与迁移 7c1e4d2b9a53 都调用 apply。
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass

from app.services.catalog import haversine_km

QUALITY_VERSION = 1
PLACEHOLDER_MIN_PLANTS = 3
WRI_COVER_KM = 20.0
WRI_AGGREGATE_KW = 500_000.0
WRI_AGGREGATE_KM = 50.0
WRI_REGION_KM = 50.0
DUPLICATE = "duplicate"

_CJK = re.compile(r"[一-鿿]")
_PHASE_TOKEN = re.compile(r"\b(?:[IVX]{1,4}|\d{1,2})\b")


@dataclass
class Report:
    relocated: int = 0
    unresolved: int = 0
    duplicates: int = 0
    regions: int = 0
    renamed: int = 0


def _phases(p) -> list[dict]:
    return (p.provenance or {}).get("phases") or []


def _original(p) -> tuple[float, float]:
    orig = ((p.provenance or {}).get("location") or {}).get("original")
    return (float(orig[0]), float(orig[1])) if orig else (p.latitude, p.longitude)


def _set_provenance(p, key: str, value) -> None:
    prov = dict(p.provenance or {})  # 换新对象，JSON 列才会被识别为已修改
    if value is None:
        prov.pop(key, None)
    else:
        prov[key] = value
    p.provenance = prov or None  # WRI 原本没有溯源，删空后回到 null 而不是 {}


# ────────────────────────────── 1. 占位坐标 ──────────────────────────────


def locate(plants, report: Report) -> None:
    gem = [p for p in plants if p.source == "gem" and p.status == "operating"]
    groups: dict[tuple[float, float], list] = defaultdict(list)
    for p in gem:
        lat, lon = _original(p)
        groups[(round(lat, 4), round(lon, 4))].append(p)
    tier: dict[str, str | None] = {}
    for members in groups.values():
        t = None
        if len(members) >= PLACEHOLDER_MIN_PLANTS:
            cities = {m.city for m in members if m.city}
            districts = {m.district for m in members if m.district}
            t = "province" if len(cities) > 1 else "city" if len(districts) > 1 else None
        for m in members:
            tier[m.id] = t
    by_district: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    by_city: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    for p in gem:
        if tier[p.id] is None:
            point = _original(p)
            if p.district:
                by_district[(p.province, p.city, p.district)].append(point)
            if p.city:
                by_city[(p.province, p.city)].append(point)

    for p in gem:
        lat0, lon0 = _original(p)
        t = tier[p.id]
        if t is None:
            if (p.provenance or {}).get("location"):  # 目录更新后不再是占位：恢复原坐标
                p.latitude, p.longitude = lat0, lon0
                _set_provenance(p, "location", None)
            continue
        refs, method = [], None
        if p.district and by_district.get((p.province, p.city, p.district)):
            refs, method = by_district[(p.province, p.city, p.district)], "district"
        elif t == "province" and p.city and by_city.get((p.province, p.city)):
            refs, method = by_city[(p.province, p.city)], "city"
        if refs:
            p.latitude = round(statistics.median(r[0] for r in refs), 4)
            p.longitude = round(statistics.median(r[1] for r in refs), 4)
            report.relocated += 1
        else:
            p.latitude, p.longitude = lat0, lon0
            report.unresolved += 1
        _set_provenance(
            p,
            "location",
            {
                "tier": t,
                "original": [lat0, lon0],
                "method": method,
                "references": len(refs),
                "version": QUALITY_VERSION,
            },
        )


# ────────────────────────────── 2–3. WRI ──────────────────────────────


def _grid(plants):
    grid: dict[tuple[int, int], list] = defaultdict(list)
    for p in plants:
        grid[(int(p.latitude // 1), int(p.longitude // 1))].append(p)
    return grid


def _near(grid, p, radius_km: float):
    """返回 [(距离 km, 场站)]。1° 网格粗筛，50 km 以内看相邻一圈就够。"""
    cy, cx = int(p.latitude // 1), int(p.longitude // 1)
    out = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for o in grid.get((cy + dy, cx + dx), []):
                d = haversine_km(p.latitude, p.longitude, o.latitude, o.longitude)
                if d <= radius_km:
                    out.append((d, o))
    return sorted(out, key=lambda t: t[0])


def wri(plants, report: Report) -> None:
    gem = [p for p in plants if p.source == "gem" and p.status == "operating"]
    grid_all = _grid(gem)
    grid_type = {t: _grid([p for p in gem if p.type == t]) for t in ("solar", "wind")}
    for w in plants:
        if w.source != "wri" or w.status not in ("operating", DUPLICATE):
            continue
        near = _near(grid_type.get(w.type, {}), w, WRI_AGGREGATE_KM)
        cover20 = sum(o.capacity_kw for d, o in near if d <= WRI_COVER_KM)
        cover50 = sum(o.capacity_kw for _, o in near)
        rule = None
        if cover20 >= w.capacity_kw:
            rule, radius, covered = "20 km 内同类型 GEM 容量合计不小于本条", WRI_COVER_KM, cover20
        elif w.capacity_kw >= WRI_AGGREGATE_KW and cover50 >= w.capacity_kw:
            rule, radius, covered = "500 MW 以上汇总条目，50 km 内被覆盖", WRI_AGGREGATE_KM, cover50
        if rule:
            w.status = DUPLICATE
            _set_provenance(
                w,
                "duplicate",
                {
                    "rule": rule,
                    "radius_km": radius,
                    "covered_kw": covered,
                    "nearest": [o.id for d, o in near if d <= radius][:3],
                    "version": QUALITY_VERSION,
                },
            )
            report.duplicates += 1
            continue
        if w.status == DUPLICATE:
            w.status = "operating"
            _set_provenance(w, "duplicate", None)
        if w.province and not (w.provenance or {}).get("region_from"):
            continue
        nearest = _near(grid_all, w, WRI_REGION_KM)
        if nearest:
            d, g = nearest[0]
            w.province, w.city = g.province, g.city
            _set_provenance(w, "region_from", {"id": g.id, "distance_km": round(d, 1)})
            report.regions += 1


# ────────────────────────────── 4. 名称 ──────────────────────────────


def merged_site_name(phases: list[dict], kind: str, region: str) -> str:
    """多期合并场址的名称：最大一期的中文名「等 N 期」；各期都没有中文名时用区域兜底。"""
    named = [(ph.get("capacity_kw") or 0, ph.get("name") or "") for ph in phases]
    best = max(named, key=lambda t: t[0])[1] if named else ""
    if _CJK.search(best):
        return f"{best}等{len(phases)}期"
    return f"{region}{'光伏' if kind == 'solar' else '风电'}场址（{len(phases)}期合计）"


def rename(plants, report: Report) -> None:
    active = [p for p in plants if p.status == "operating"]
    base: dict[str, str | None] = {}
    for p in active:
        source_name = (p.provenance or {}).get("name_local_source", p.name_local)
        phases = _phases(p)
        if p.source == "gem" and len(phases) > 1:
            region = p.district or p.city or p.province or "公开"
            base[p.id] = merged_site_name(phases, p.type, region)
        else:
            base[p.id] = source_name
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for p in active:
        if base[p.id]:
            groups[(p.type, base[p.id])].append(p)
    final = dict(base)
    for (_, name), members in groups.items():
        if len(members) < 2:
            continue
        tokens = [(_PHASE_TOKEN.findall(m.name or "") or [None])[-1] for m in members]
        capacities = [round(m.capacity_kw / 1000, 3) for m in members]
        if all(tokens) and len(set(tokens)) == len(tokens):
            suffixes = tokens
        elif len(set(capacities)) == len(capacities):
            suffixes = [f"{c:g} MW" for c in capacities]
        else:
            order = {m.id: i + 1 for i, m in enumerate(sorted(members, key=lambda m: m.id))}
            suffixes = [str(order[m.id]) for m in members]
        for m, s in zip(members, suffixes, strict=True):
            final[m.id] = f"{name}（{s}）"
    for p in active:
        new = final[p.id]
        if new == p.name_local:
            continue
        if "name_local_source" not in (p.provenance or {}):
            _set_provenance(p, "name_local_source", p.name_local)
        p.name_local = new[:128] if new else new
        report.renamed += 1


# ────────────────────────────── 入口 ──────────────────────────────


def apply(plants) -> Report:
    """plants 为全部目录行（任意状态）。直接修改对象，不提交。"""
    plants = list(plants)
    report = Report()
    locate(plants, report)
    wri(plants, report)
    rename(plants, report)
    return report


async def apply_db(db) -> Report:
    from sqlalchemy import select

    from app.models import CatalogPlant

    rows = (await db.execute(select(CatalogPlant))).scalars().all()
    return apply(rows)
