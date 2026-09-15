"""REIT 场站电量对账：公开披露的实际发电量 vs 模型在 ERA5 上算出的可发电量。docs/07 §八

数据：scripts/reit_disclosures.json（交易所公告原文整理，每个数字带出处与原文片段）。
气象：Open-Meteo archive（ERA5），统一走 providers.weather_transport.weather_get，
      原始响应按站点、年份缓存在 data/calibration/reit/。

两套参数：
  default  目录默认：光伏按公告直流 MWp、容配比换交流，倾角=纬度、正南、固定支架；
           风电轮毂 settings 默认高度，机型档与线上目录同一规则（prediction_basis.wind_turbine_class）
  known    公告已知参数：交流容量、支架类型、倾角、轮毂高度、额定风速对应的机型档

对比口径：
  发电量 / 模型            发电量未扣送出线损与综合厂用电，是最接近模型出力的披露口径
  (发电量 + 限电) / 模型   只在披露了限电损失电量的期间给出
模型含场站损耗（光伏 14%、风电 10%），不含限电；比值 < 1 表示模型偏高。

用法：
  uv run python scripts/reit_backtest.py
  uv run python scripts/reit_backtest.py --end 2026-06-30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.models import Station  # noqa: E402
from app.providers.weather_transport import weather_get  # noqa: E402
from app.services import energy, weather  # noqa: E402
from app.services.prediction_basis import VERSION, wind_turbine_class  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATASET = Path(__file__).with_name("reit_disclosures.json")
DATA_DIR = ROOT / "data" / "calibration" / "reit"
REPORT_DIR = ROOT.parents[1] / "docs" / "reports"
TZ = "Asia/Shanghai"
CACHE_VERSION = "v1"
ARCHIVE_VARS = [
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "temperature_2m",
    "wind_speed_10m",
    "wind_speed_100m",  # ERA5 只有 10 / 100 m 两层
    "surface_pressure",
]
MIN_COVERAGE = 0.98  # 期间内可算小时占比低于此值不进汇总
RETRIES = 3  # 本机系统代理下出网偶发拒连，与线上 upstream_retries 同量级
VARIANTS = ("default", "known")


# ────────────────────────────── 数据 ──────────────────────────────


@dataclass
class Plant:
    id: str
    name: str
    reit: str
    kind: str  # solar | wind
    province: str
    lat: float
    lon: float
    coord_source: str
    capacity_mw: float
    capacity_basis: str  # AC | DC
    capacity_ac_mw: float | None
    capacity_dc_mw: float | None  # 光伏公告直流容量，known 方案用
    commissioning_year: int | None  # default 方案按线上目录规则选风电机型档
    # Open-Meteo 格点选择。默认 land 会把近岸海上风电分到陆地格点，10 m 风速偏低三成，
    # 海上站填 sea，与线上目录一致（prediction_basis.catalog_cell_selection）
    cell_selection: str | None
    mounting: str | None
    tilt: float | None
    bifacial: bool | None
    hub_height: float | None
    rated_wind_speed: float | None
    specific_power: float | None  # 单机容量 / 扫风面积 W/m²，混装按容量加权
    periods: list[dict] = field(default_factory=list)
    # 合计口径：公告只披露多个项目合计电量时，按成员各自位置与参数建模后逐时相加
    members: list[str] = field(default_factory=list)


def load_dataset(path: Path) -> list[Plant]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    plants: dict[str, Plant] = {}
    for s in raw["stations"]:
        members = list(s.get("members") or [])
        if not members and (s.get("lat") is None or s.get("lon") is None):
            print(f"  跳过 {s['id']}：没有坐标")
            continue
        pv = s.get("pv") or {}
        wd = s.get("wind") or {}
        plants[s["id"]] = Plant(
            id=s["id"],
            name=s.get("short_name") or s["name"],
            reit=s["reit"],
            kind="solar" if s["type"] == "pv" else "wind",
            province=s.get("province") or "",
            lat=float(s["lat"]) if s.get("lat") is not None else float("nan"),
            lon=float(s["lon"]) if s.get("lon") is not None else float("nan"),
            coord_source=s.get("coord_source") or "",
            capacity_mw=float(s["capacity_mw"]),
            capacity_basis=(s.get("capacity_basis") or "unknown").upper(),
            capacity_ac_mw=s.get("capacity_ac_mw"),
            capacity_dc_mw=s.get("capacity_dc_mwp"),
            commissioning_year=int(str(s["commissioning"])[:4]) if s.get("commissioning") else None,
            cell_selection=s.get("cell_selection"),
            mounting=pv.get("mounting"),
            tilt=pv.get("tilt_deg"),
            bifacial=pv.get("bifacial"),
            hub_height=wd.get("hub_height_m"),
            rated_wind_speed=wd.get("rated_wind_speed_ms"),
            specific_power=wd.get("specific_power_w_m2"),
            members=members,
        )
    for gid in [k for k, p in plants.items() if p.members]:
        missing = [m for m in plants[gid].members if m not in plants or plants[m].members]
        if missing:
            print(f"  跳过 {gid}：成员 {missing} 缺坐标或不是单站")
            del plants[gid]
    for p in raw["periods"]:
        if p["station_id"] in plants:
            plants[p["station_id"]].periods.append(p)
    return list(plants.values())


async def fetch_year(
    http: httpx.AsyncClient, plant: Plant, start: date, end: date
) -> tuple[pd.DataFrame, float | None]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cell = f"_{plant.cell_selection}" if plant.cell_selection else ""
    cache = DATA_DIR / (
        f"archive_{CACHE_VERSION}_{plant.id}_{plant.lat:.4f}_{plant.lon:.4f}{cell}_{start}_{end}.json"
    )
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        params = {
            "latitude": plant.lat,
            "longitude": plant.lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": ",".join(ARCHIVE_VARS),
            "timezone": TZ,
            "wind_speed_unit": "ms",
        }
        if plant.cell_selection:
            params["cell_selection"] = plant.cell_selection
        url = f"{settings.open_meteo_archive_base}/archive"
        for attempt in range(RETRIES + 1):  # 连接错误与 5xx 退避重试；4xx（含 429）不重试
            try:
                res = await weather_get(http, url, params=params, timeout=180)
            except httpx.TransportError:
                if attempt == RETRIES:
                    raise
            else:
                if res.status_code < 500 or attempt == RETRIES:
                    break
            await asyncio.sleep(2**attempt)
        res.raise_for_status()
        raw = res.json()
        cache.write_text(json.dumps(raw))
    h = raw["hourly"]
    idx = pd.DatetimeIndex(pd.to_datetime(h["time"])).tz_localize(TZ)
    df = pd.DataFrame({k: v for k, v in h.items() if k != "time"}, index=idx).astype(float)
    elev = raw.get("elevation")
    return df, float(elev) if isinstance(elev, int | float) else None


async def fetch_archive(
    http: httpx.AsyncClient, plant: Plant, start: date, end: date
) -> weather.Forecast:
    """按自然年分段拉取并缓存，避免单次请求过长；前后各多取一天给缺口插值与区间末标签。"""
    lo, hi = start - timedelta(days=1), end + timedelta(days=1)
    frames, elev = [], None
    for year in range(lo.year, hi.year + 1):
        a, b = max(lo, date(year, 1, 1)), min(hi, date(year, 12, 31))
        df, e = await fetch_year(http, plant, a, b)
        frames.append(df)
        elev = elev if elev is not None else e
    data = pd.concat(frames)
    data = data[~data.index.duplicated()].sort_index()
    return weather.Forecast(tz=TZ, hourly=data, model="era5", elevation=elev)


# ────────────────────────────── 模型 ──────────────────────────────


def rated_from_specific_power(sp: float | None) -> float | None:
    """比功率估额定风速：P/A = ½ ρ Cp v³，取 ρ 1.225、额定点 Cp 0.42。
    210 W/m² ≈ 9.3 m/s，300 ≈ 10.5，370 ≈ 11.3，与主流机型样本量级一致，只用于选机型档。"""
    if sp is None:
        return None
    return (2 * sp / (settings.wind_air_density_ref * 0.42)) ** (1 / 3)


def _turbine_class(rated: float | None) -> str | None:
    if rated is None:
        return None
    if rated <= 10.0:
        return "low_wind"
    if rated <= 11.5:
        return "medium_wind"
    if rated >= 12.5:
        return "high_wind"
    return None


def make_station(plant: Plant, variant: str) -> Station:
    st = Station(
        id=f"reit-{plant.id}",
        owner_id="reit",
        name=plant.name,
        type=plant.kind,
        latitude=plant.lat,
        longitude=plant.lon,
        capacity_kw=plant.capacity_mw * 1000,
        tilt=None,
        azimuth=None,
        hub_height=None,
    )
    ratio = settings.pv_dc_ac_ratio
    if plant.kind == "solar":
        if plant.capacity_basis == "AC":
            ac = plant.capacity_mw * 1000
            dc = ac * ratio
        else:  # 公告光伏容量均按 MWp 标，按直流处理；与目录 catalog_basis 的 dc 分支一致
            dc = plant.capacity_mw * 1000
            ac = dc / ratio
        if variant == "known":
            if plant.capacity_dc_mw:
                dc = plant.capacity_dc_mw * 1000
            if plant.capacity_ac_mw:
                ac = plant.capacity_ac_mw * 1000
            if plant.mounting == "single_axis":
                st.mounting = "single_axis"
            if plant.tilt is not None:
                st.tilt = plant.tilt
            if plant.bifacial:
                st.bifacial = True
        st.capacity_kw = ac
        st._pv_capacity = (dc, ac)
    elif variant == "default":
        # 与线上目录同一条规则：陆上按投运年份、海上通用曲线，见 prediction_basis
        st.turbine_class = wind_turbine_class(
            plant.commissioning_year, plant.cell_selection == "sea"
        )
    else:
        st.hub_height = plant.hub_height
        rated = plant.rated_wind_speed or rated_from_specific_power(plant.specific_power)
        st.turbine_class = _turbine_class(rated)
    return st


def _hours(kind: str, d0: date, d1: date) -> pd.DatetimeIndex:
    """光伏 v4 为区间末标签（01:00 … 次日 00:00），风电为整点瞬时（00:00 … 23:00）。"""
    start = pd.Timestamp(d0, tz=TZ)
    if kind == "solar":
        start += pd.Timedelta(hours=1)
    return pd.date_range(start, periods=((d1 - d0).days + 1) * 24, freq="h")


def model_series(
    st: Station, fc: weather.Forecast, start: date, end: date
) -> tuple[pd.Series, pd.Series]:
    """按月分段走线上同一套 prepare → hourly_power，某月不可算只影响该月。

    返回 (逐时出力 kW, 气象对照量)：光伏为 GHI W/m²，风电为轮毂风速 m/s。
    """
    power, aux = [], []
    m0 = date(start.year, start.month, 1)
    while m0 <= end:
        m1 = (pd.Timestamp(m0) + pd.offsets.MonthEnd(0)).date()
        d0, d1 = max(m0, start), min(m1, end)
        hours = _hours(st.type, d0, d1)
        prep = energy.prepare(st, fc, hours=hours, version=VERSION)
        power.append(energy.hourly_power(st, prep, TZ))
        if st.type == "wind":
            aux.append(prep.v_hub if prep.v_hub is not None else pd.Series(np.nan, index=hours))
        else:
            aux.append(prep.frame["shortwave_radiation"].astype(float))
        m0 = m1 + timedelta(days=1)
    return pd.concat(power), pd.concat(aux)


# ────────────────────────────── 对账 ──────────────────────────────

_GRAIN = [(re.compile(r"^\d{4}-\d{2}$"), 0), (re.compile(r"^\d{4}Q[1-4]$"), 1),
          (re.compile(r"^\d{4}H[12]$"), 2), (re.compile(r"^\d{4}$"), 3)]  # fmt: skip


def grain(period: str) -> int:
    for pat, rank in _GRAIN:
        if pat.match(period):
            return rank
    return 9  # 非标准期间（如上市首期），最后才用


def dedupe(periods: list[dict]) -> list[dict]:
    """同站同期多份文件：取第一条有发电量的记录，其余数字不同的记为冲突。"""
    by_key: dict[tuple, dict] = {}
    for p in periods:
        key = (p["period"], p["start"], p["end"])
        cur = by_key.get(key)
        if cur is None or (
            cur.get("generation_mwh") is None and p.get("generation_mwh") is not None
        ):
            if cur is not None:
                p = {**p, "_conflict": cur.get("_conflict")}
            by_key[key] = dict(p)
            continue
        g0, g1 = cur.get("generation_mwh"), p.get("generation_mwh")
        if g0 is not None and g1 is not None and abs(g0 - g1) > 0.005 * max(abs(g0), 1):
            cur["_conflict"] = f"另一份文件为 {g1:,.0f} MWh"
        for k in ("curtailed_mwh", "wind_speed_ms", "irradiance_mj_m2", "settled_mwh"):
            if cur.get(k) is None and p.get(k) is not None:
                cur[k] = p[k]
    return sorted(by_key.values(), key=lambda p: (p["start"], grain(p["period"])))


def select_disjoint(rows: pd.DataFrame) -> pd.DataFrame:
    """汇总用不重叠的期间：粒度细的优先（月 > 季 > 半年 > 年），同粒度按时间。"""
    ok = rows[
        rows["generation_mwh"].notna() & (rows["coverage"] >= MIN_COVERAGE) & rows["exclude"].isna()
    ]
    ok = ok.sort_values(["grain", "start"])
    taken: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    keep = []
    for i, r in ok.iterrows():
        if all(r["end"] < a or r["start"] > b for a, b in taken):
            taken.append((r["start"], r["end"]))
            keep.append(i)
    return ok.loc[keep].sort_values("start")


Series = dict[str, tuple[pd.Series, pd.Series]]  # variant → (逐时出力 kW, 气象对照量)


def plant_series(plant: Plant, fc: weather.Forecast, start: date, end: date) -> Series:
    return {v: model_series(make_station(plant, v), fc, start, end) for v in VARIANTS}


def group_series(parts: list[Series]) -> Series:
    """合计口径：成员逐时出力相加，任一成员缺测该时刻即缺测；气象对照量取成员平均。"""
    out: Series = {}
    for v in VARIANTS:
        kw = pd.concat([p[v][0] for p in parts], axis=1)
        aux = pd.concat([p[v][1] for p in parts], axis=1)
        out[v] = (kw.sum(axis=1, min_count=kw.shape[1]), aux.mean(axis=1))
    return out


def usable_periods(plant: Plant, cutoff: date) -> list[dict]:
    return [p for p in dedupe(plant.periods) if date.fromisoformat(p["end"]) <= cutoff]


def evaluate(plant: Plant, periods: list[dict], series: Series) -> pd.DataFrame:
    rows = []
    for p in periods:
        d0, d1 = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
        hours = _hours(plant.kind, d0, d1)
        row = {
            "plant": plant.id,
            "period": p["period"],
            "grain": grain(p["period"]),
            "start": pd.Timestamp(d0),
            "end": pd.Timestamp(d1),
            "generation_mwh": p.get("generation_mwh"),
            "settled_mwh": p.get("settled_mwh"),
            "curtailed_mwh": p.get("curtailed_mwh"),
            "disclosed_wind_ms": p.get("wind_speed_ms"),
            "disclosed_irr_mj": p.get("irradiance_mj_m2"),
            "conflict": p.get("_conflict"),
            "exclude": p.get("exclude"),  # 不进汇总的原因：并网不满、停电检修、事件期等
            "source_url": p.get("source_url"),
        }
        for v in VARIANTS:
            kw, aux = series[v]
            seg = kw.reindex(hours)
            row[f"model_{v}_mwh"] = float(seg.sum(skipna=True)) / 1000
            if v == "default":
                row["coverage"] = float(seg.notna().mean())
                a = aux.reindex(hours)
                if plant.kind == "wind":
                    row["era5_wind_ms"] = float(a.mean())
                else:
                    row["era5_ghi_mj"] = float(a.sum(skipna=True)) * 3600 / 1e6
        rows.append(row)
    return pd.DataFrame(rows)


def _ratio(num: float | None, den: float) -> float | None:
    if num is None or not np.isfinite(num) or den <= 0:
        return None
    return num / den


def _pct(x: float | None) -> str:
    return "—" if x is None or not np.isfinite(x) else f"{x:.0%}"


def _mwh(x: float | None) -> str:
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.0f}"


# ────────────────────────────── 主流程 ──────────────────────────────


async def run(cutoff: date, dataset: Path) -> int:
    plants = load_dataset(dataset)
    print(f"{len(plants)} 座场站，截止 {cutoff}，计算版本 {VERSION}")
    periods = {p.id: usable_periods(p, cutoff) for p in plants}
    need: dict[str, list[date]] = {}  # 单站需要算的日期范围，含其所属合计口径的期间
    for p in plants:
        for pid in p.members or [p.id]:
            for q in periods[p.id]:
                need.setdefault(pid, []).extend(
                    [date.fromisoformat(q["start"]), date.fromisoformat(q["end"])]
                )
    series: dict[str, Series] = {}
    async with httpx.AsyncClient() as http:
        for p in plants:
            if p.members or p.id not in need:
                continue
            start, end = min(need[p.id]), max(need[p.id])
            print(f"[{p.name}] {start} ~ {end}", flush=True)
            fc = await fetch_archive(http, p, start, end)
            series[p.id] = await asyncio.to_thread(plant_series, p, fc, start, end)
    tables = []
    for p in plants:
        if not periods[p.id]:
            continue
        s = group_series([series[m] for m in p.members]) if p.members else series[p.id]
        tables.append(evaluate(p, periods[p.id], s))
    rows = pd.concat(tables, ignore_index=True)
    by_id = {p.id: p for p in plants}

    summary_lines, period_lines, year_lines = [], [], []
    for pid, g in rows.groupby("plant", sort=False):
        p = by_id[pid]
        sel = select_disjoint(g)
        act = float(sel["generation_mwh"].sum()) if len(sel) else None
        md = float(sel["model_default_mwh"].sum()) if len(sel) else 0.0
        mk = float(sel["model_known_mwh"].sum()) if len(sel) else 0.0
        curt = sel[sel["curtailed_mwh"].notna()]
        pot = (
            _ratio(float((curt["generation_mwh"] + curt["curtailed_mwh"]).sum()),
                   float(curt["model_default_mwh"].sum()))
            if len(curt) else None
        )  # fmt: skip
        span = f"{sel['start'].min():%Y-%m} ~ {sel['end'].max():%Y-%m}" if len(sel) else "—"
        cap = (
            f"{p.capacity_mw:g} {'MWp' if p.kind == 'solar' and p.capacity_basis != 'AC' else 'MW'}"
        )
        summary_lines.append(
            f"| {p.name} | {p.reit} | {'光伏' if p.kind == 'solar' else '风电'} | {p.province} | {cap} | "
            f"{span}（{len(sel)} 期） | {_mwh(act)} | {_mwh(md)} | {_pct(_ratio(act, md))} | "
            f"{_pct(_ratio(act, mk))} | {_pct(pot)} |"
        )
        for yr, yg in sel.groupby(sel["start"].dt.year):
            full = (yg["end"] - yg["start"]).dt.days.add(1).sum() >= 360
            year_lines.append(
                f"| {p.name} | {yr}{'' if full else '（不全）'} | {_mwh(yg['generation_mwh'].sum())} | "
                f"{_mwh(yg['model_default_mwh'].sum())} | "
                f"{_pct(_ratio(yg['generation_mwh'].sum(), yg['model_default_mwh'].sum()))} |"
            )
        for _, r in g.sort_values(["start", "grain"]).iterrows():
            if p.kind == "wind":
                met = f"{r['disclosed_wind_ms']:.2f} / {r['era5_wind_ms']:.2f} m/s" if pd.notna(
                    r["disclosed_wind_ms"]) else f"— / {r['era5_wind_ms']:.2f} m/s"  # fmt: skip
            else:
                met = f"{r['disclosed_irr_mj']:,.0f} / {r['era5_ghi_mj']:,.0f} MJ/m²" if pd.notna(
                    r["disclosed_irr_mj"]) else f"— / {r['era5_ghi_mj']:,.0f} MJ/m²"  # fmt: skip
            flag = [] if r["coverage"] >= MIN_COVERAGE else [f"可算 {r['coverage']:.0%}"]
            if isinstance(r["exclude"], str):
                flag.append(f"不进汇总：{r['exclude']}")
            if isinstance(r["conflict"], str):
                flag.append(r["conflict"])
            period_lines.append(
                f"| {p.name} | {r['period']} | {_mwh(r['generation_mwh'])} | {_mwh(r['curtailed_mwh'])} | "
                f"{_mwh(r['model_default_mwh'])} | {_pct(_ratio(r['generation_mwh'], r['model_default_mwh']))} | "
                f"{_pct(_ratio(r['generation_mwh'], r['model_known_mwh']))} | {met} | {'；'.join(flag)} |"
            )

    lines = [
        f"# REIT 场站电量对账 {VERSION} · {date.today()}\n",
        "实际值取公募 REIT 公告披露的**发电量**（未扣送出线损与综合厂用电），模型值为同地点、同期间在 "
        "Open-Meteo archive（ERA5）上走线上同一条链路（`services.energy.prepare → hourly_power`）算出的可发电量，"
        f"含场站损耗（光伏 {settings.pv_losses:.0%}、风电 {settings.wind_losses:.0%}），不含限电。\n",
        "- **default**：目录默认参数。光伏按公告直流 MWp、容配比 "
        f"{settings.pv_dc_ac_ratio} 换交流，倾角=纬度、正南、固定支架；风电轮毂 "
        f"{settings.wind_hub_height_default:.0f} m，机型档与线上目录同一规则：陆上 "
        f"{settings.wind_catalog_modern_from_year} 年起投运按 {settings.wind_catalog_modern_class}，"
        f"更早投运与海上按通用功率曲线 {settings.wind_v_in:g}/{settings.wind_v_rated:g}/"
        f"{settings.wind_v_out:g} m/s，海上取海上格点（docs/07 §2.2）。",
        "- **known**：公告已知参数（直流 / 交流容量、支架、倾角、轮毂高度）；风电机型档按公告额定风速，"
        "没有时按单机容量与叶轮直径算出的比功率估额定风速（≤ 10 m/s 低风速档、≤ 11.5 m/s 中风速档），"
        "公告没写的项同 default。",
        "- 比值 = 实际 ÷ 模型，**小于 100% 表示模型偏高**。「含限电」= (发电量 + 披露的限电损失) ÷ 模型 default。",
        f"- 汇总只用不重叠、可算小时 ≥ {MIN_COVERAGE:.0%} 的期间，粒度细的优先。\n",
        "数据与出处：`packages/server/scripts/reit_disclosures.json`；脚本 `packages/server/scripts/reit_backtest.py`。\n",
        "## 1. 场站汇总\n",
        "| 场站 | REIT | 类型 | 省 | 装机 | 期间 | 实际发电 MWh | 模型 default MWh | 实际/default | "
        "实际/known | 含限电/default |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *summary_lines,
        "\n## 2. 分年\n",
        "| 场站 | 年 | 实际发电 MWh | 模型 default MWh | 实际/default |",
        "| --- | --- | --- | --- | --- |",
        *year_lines,
        "\n## 3. 逐期\n",
        "气象对照：风电为「公告平均风速 / ERA5 轮毂风速均值」，光伏为「公告辐射量 / ERA5 水平面总辐射」。"
        "公告测点与平面未说明，只看方向与量级。\n",
        "| 场站 | 期间 | 实际发电 MWh | 限电 MWh | 模型 default MWh | 实际/default | 实际/known | 气象 公告/ERA5 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *period_lines,
    ]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"reit-backtest-{date.today()}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows.to_csv(DATA_DIR / f"reit_periods_{VERSION}.csv", index=False)
    print(f"\n报告：{out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", type=date.fromisoformat, help="只对账截止此日期前结束的期间")
    ap.add_argument("--dataset", type=Path, default=DATASET)
    args = ap.parse_args()
    cutoff = args.end or (date.today() - timedelta(days=7))  # archive 约 5 天延迟
    return asyncio.run(run(cutoff, args.dataset))


if __name__ == "__main__":
    raise SystemExit(main())
