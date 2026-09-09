"""指数与发电估算校准。docs/07 §八

三步用同一批历史数据一次跑完：
  1. 发电量准确度：光伏年发电量与 PVGIS 对比，判据 ±15%
  2. 指数分布：5 个气候区 × 365 天，看分档分布是否过度集中
  3. 分档直觉：抽查典型晴天 / 阴天 / 雨天
风电：轮毂风速按 ERA5 10 m / 100 m 两层对数廓线插值（线上用 Open-Meteo 的 10/80/100/120 m），
同时给出旧方法（10 m 固定幂律外推、无场站损耗）作对照，并在 5 个风电基地看容量因子分布。

用法：
  uv run python scripts/calibrate.py                      # 默认最近一整年
  uv run python scripts/calibrate.py --start 2025-09-01 --end 2026-08-31
  uv run python scripts/calibrate.py --thresholds 85,70,55 --alpha 0.2   # 试参数，不改代码

数据：Open-Meteo archive（ERA5 再分析），原始响应缓存在 data/calibration/。
PVGIS 用 PVGIS-ERA5 库 2005–2023 多年平均，与单年结果天然有差异，判据留了余量。
报告写到 docs/reports/index-calibration-<日期>.md。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.metrics import wind  # noqa: E402
from app.metrics.index import PvInputs, pv_index, wind_index  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "calibration"
REPORT_DIR = ROOT.parents[1] / "docs" / "reports"

PV_CAPACITY_KW = 500.0  # 交流侧；直流侧 = × settings.pv_dc_ac_ratio，与 PVGIS 的 kWp 对账时按此换算
WIND_CAPACITY_KW = 2000.0
PVGIS_TOLERANCE = 0.15
TZ = "Asia/Shanghai"
CACHE_VERSION = "v2"

ARCHIVE_VARS = [
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "temperature_2m",
    "wind_speed_10m",
    "cloud_cover",
    "precipitation",
    "wind_speed_100m",  # ERA5 有 100 m 风，用来标定 10 m → 轮毂高度的幂律指数
]
LEVELS = ("excellent", "good", "fair", "poor")


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    zone: str
    lat: float
    lon: float


# 光伏：5 个气候区，覆盖辐射与云量的主要梯度
PV_SITES = [
    Site("suzhou", "苏州", "华东·亚热带湿润", 31.30, 120.62),
    Site("guangzhou", "广州", "华南·多云多雨", 23.13, 113.26),
    Site("beijing", "北京", "华北·温带半湿润", 39.90, 116.40),
    Site("lhasa", "拉萨", "青藏高原·高辐射", 29.65, 91.13),
    Site("dunhuang", "敦煌", "西北·干旱", 40.14, 94.66),
]
# 风电：城市不是风场选址，用真实风电基地所在地，否则容量因子全是个位数
WIND_SITES = [
    Site("guazhou", "瓜州", "甘肃酒泉风电基地·戈壁", 40.52, 95.78),
    Site("zhangbei", "张北", "河北坝上·草原台地", 41.16, 114.70),
    Site("dabancheng", "达坂城", "新疆·山口风区", 43.35, 88.31),
    Site("tongliao", "通辽", "内蒙古东部·平原", 43.62, 122.26),
    Site("pingtan", "平潭", "福建沿海·海岛", 25.50, 119.79),
]


# ────────────────────────────── 数据 ──────────────────────────────


def fetch_archive(http: httpx.Client, site: Site, start: date, end: date) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache = DATA_DIR / f"archive_{CACHE_VERSION}_{site.key}_{start}_{end}.json"
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        res = http.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": site.lat,
                "longitude": site.lon,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hourly": ",".join(ARCHIVE_VARS),
                "timezone": TZ,
                "wind_speed_unit": "ms",  # 默认是 km/h，漏了这个风电全错
            },
            timeout=120,
        )
        res.raise_for_status()
        raw = res.json()
        cache.write_text(json.dumps(raw))
    h = raw["hourly"]
    idx = pd.DatetimeIndex(pd.to_datetime(h["time"])).tz_localize(TZ)
    df = pd.DataFrame({k: v for k, v in h.items() if k != "time"}, index=idx)
    return df.astype(float)


def fetch_pvgis(http: httpx.Client, site: Site) -> dict | None:
    cache = DATA_DIR / f"pvgis_{site.key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    try:
        res = http.get(
            "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc",
            params={
                "lat": site.lat,
                "lon": site.lon,
                "peakpower": PV_CAPACITY_KW,
                "loss": round(settings.pv_losses * 100),
                "angle": round(abs(site.lat), 1),  # 与 pv.default_tilt 一致
                "aspect": 0,  # PVGIS 的 0 = 正南，对应我们的 180
                "outputformat": "json",
            },
            timeout=60,
        )
        res.raise_for_status()
        d = res.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  PVGIS 拉取失败 {site.name}: {exc}")
        return None
    cache.write_text(json.dumps(d))
    return d


# ────────────────────────────── 模型 ──────────────────────────────


def run_pv_days(site: Site, df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day, chunk in df.groupby(df.index.date):
        if len(chunk) < 24:
            continue
        inp = PvInputs(
            latitude=site.lat,
            longitude=site.lon,
            tz=TZ,
            capacity_kw=PV_CAPACITY_KW,
            tilt=abs(site.lat),
            azimuth=180.0,
            times=pd.DatetimeIndex(chunk.index),
            ghi=chunk["shortwave_radiation"].fillna(0.0),
            dni=chunk["direct_normal_irradiance"].fillna(0.0),
            dhi=chunk["diffuse_radiation"].fillna(0.0),
            temp_air=chunk["temperature_2m"].ffill().bfill(),
            wind_speed=chunk["wind_speed_10m"].fillna(0.0),
        )
        r = pv_index(inp)
        rows.append(
            {
                "date": pd.Timestamp(day),
                "score": r.score,
                "actual_kwh": r.actual_kwh,
                "ideal_kwh": r.ideal_kwh,
                "ghi_sum": float(chunk["shortwave_radiation"].sum()),
                "cloud_mean": float(chunk["cloud_cover"].mean()),
                "precip": float(chunk["precipitation"].sum()),
                "temp_mean": float(chunk["temperature_2m"].mean()),
            }
        )
    return pd.DataFrame(rows).set_index("date")


def run_wind_days(df: pd.DataFrame, alpha: float) -> pd.DataFrame:
    """本模型：10 m / 100 m 两层对数廓线插值到轮毂 + 场站损耗（与线上一致）；
    旧方法：10 m 按固定 α 幂律外推、无损耗，作对照。"""
    hub = wind.default_hub_height()
    rows = []
    for day, chunk in df.groupby(df.index.date):
        if len(chunk) < 24:
            continue
        v10 = chunk["wind_speed_10m"].fillna(0.0)
        v100 = chunk["wind_speed_100m"].fillna(0.0)
        v_hub, _ = wind.hub_wind_speed({10.0: v10, 100.0: v100}, hub)
        daily = float(wind.plant_power(v_hub, WIND_CAPACITY_KW).sum())
        v_old = v10 * (hub / 10.0) ** alpha
        daily_old = float(wind.power_curve(v_old, WIND_CAPACITY_KW).sum())
        rows.append(
            {
                "date": pd.Timestamp(day),
                "score": wind_index(daily, WIND_CAPACITY_KW).score,
                "score_old": wind_index(daily_old, WIND_CAPACITY_KW).score,
                "daily_kwh": daily,
                "daily_kwh_old": daily_old,
                "v10_mean": float(v10.mean()),
                "v100_mean": float(chunk["wind_speed_100m"].mean()),
            }
        )
    return pd.DataFrame(rows).set_index("date")


def fit_shear_alpha(dfs: list[pd.DataFrame]) -> float:
    """按 ERA5 10 m 与 100 m 风速拟合幂律指数：α = ln(v100/v10) / ln(10)。

    只用两者都 ≥ 2 m/s 的时次，避免静风时比值发散。取全部风电站点的中位数。
    """
    ratios = []
    for df in dfs:
        v10, v100 = df["wind_speed_10m"], df["wind_speed_100m"]
        ok = (v10 >= 2.0) & (v100 >= 2.0)
        ratios.append(np.log(v100[ok] / v10[ok]) / np.log(10.0))
    return float(np.median(pd.concat(ratios)))


def classify_with(score: pd.Series, th: tuple[float, float, float]) -> pd.Series:
    ex, good, fair = th
    bins = [-1, fair - 1e-9, good - 1e-9, ex - 1e-9, 101]
    return pd.cut(score, bins=bins, labels=["poor", "fair", "good", "excellent"]).astype(str)


def level_dist(levels: pd.Series) -> dict[str, float]:
    n = len(levels)
    return {k: round(float((levels == k).sum()) / n * 100, 1) for k in LEVELS}


def fmt_dist(d: dict[str, float]) -> str:
    return " / ".join(f"{d[k]:.0f}%" for k in LEVELS)


def spot_days(pv: pd.DataFrame) -> dict[str, pd.Series | None]:
    """典型晴天 / 阴天 / 雨天各挑一天，取夏半年避免季节干扰"""
    summer = pv[(pv.index.month >= 4) & (pv.index.month <= 9)]
    sunny = summer[summer["cloud_mean"] < 15]
    cloudy = summer[(summer["cloud_mean"] > 70) & (summer["precip"] < 0.5)]
    rainy = summer[summer["precip"] > 10]
    out: dict[str, pd.Series | None] = {}
    out["晴天"] = sunny.loc[sunny["cloud_mean"].idxmin()] if len(sunny) else None
    out["阴天"] = cloudy.loc[cloudy["ghi_sum"].idxmin()] if len(cloudy) else None
    out["雨天"] = rainy.loc[rainy["ghi_sum"].idxmin()] if len(rainy) else None
    return out


# ────────────────────────────── 主流程 ──────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--thresholds", help="excellent,good,fair，默认取 settings")
    ap.add_argument("--alpha", type=float, help="风切变指数，默认取 settings")
    args = ap.parse_args()
    end = args.end or (date.today() - timedelta(days=7))  # archive 有约 5 天延迟
    start = args.start or (end - timedelta(days=364))
    th = (
        tuple(float(x) for x in args.thresholds.split(","))
        if args.thresholds
        else (settings.index_excellent, settings.index_good, settings.index_fair)
    )
    alpha_used = args.alpha if args.alpha is not None else settings.wind_shear_alpha
    hub = wind.default_hub_height()
    kwp = PV_CAPACITY_KW * settings.pv_dc_ac_ratio
    print(f"区间 {start} ~ {end}，分档阈值 {th}，α = {alpha_used}")

    pv_rows: list[str] = []
    wind_rows: list[str] = []
    spot_rows: list[str] = []
    all_pv: dict[str, pd.DataFrame] = {}
    all_wind: dict[str, pd.DataFrame] = {}

    with httpx.Client() as http:
        for site in PV_SITES:
            print(f"[{site.name}] ", end="", flush=True)
            df = fetch_archive(http, site, start, end)
            pv = run_pv_days(site, df)
            pv["level"] = classify_with(pv["score"], th)
            all_pv[site.key] = pv

            annual = float(pv["actual_kwh"].sum()) * 365 / len(pv)
            pvgis = fetch_pvgis(http, site)
            dist = fmt_dist(level_dist(pv["level"]))
            if pvgis:
                # PVGIS 按直流峰值功率 kWp 报产量，且产量与 kWp 成正比；缓存按 PV_CAPACITY_KW 拉取，
                # 这里换算到本模型的直流侧容量
                pvgis_kwp = float(pvgis["inputs"]["pv_module"]["peak_power"])
                ref = float(pvgis["outputs"]["totals"]["fixed"]["E_y"]) * kwp / pvgis_kwp
                db = pvgis["inputs"]["meteo_data"]["radiation_db"]
                dev = (annual - ref) / ref
                ok = "✅" if abs(dev) <= PVGIS_TOLERANCE else "❌"
                ref_txt, dev_txt = f"{ref:,.0f}（{db}）", f"{dev:+.1%} {ok}"
            else:
                ref_txt, dev_txt = "—", "—"
            pv_rows.append(
                f"| {site.name} | {site.zone} | {annual:,.0f} | {ref_txt} | {dev_txt} | "
                f"{pv['score'].mean():.1f} | {dist} |"
            )
            print(
                f"年发电 {annual:,.0f} kWh，PVGIS 偏差 {dev_txt}，指数 {pv['score'].mean():.1f}，分档 {dist}"
            )

            for kind, row in spot_days(pv).items():
                if row is not None:
                    spot_rows.append(
                        f"| {site.name} | {kind} | {row.name.date()} | {row['cloud_mean']:.0f}% | "
                        f"{row['precip']:.1f} mm | {row['ghi_sum'] / 1000:.1f} | {row['score']:.0f} | "
                        f"{row['level']} |"
                    )

        wind_raw = {s.key: fetch_archive(http, s, start, end) for s in WIND_SITES}

    alpha_fit = fit_shear_alpha(list(wind_raw.values()))
    print(f"\n幂律指数：拟合 α = {alpha_fit:.3f}，本轮使用 α = {alpha_used}")
    for site in WIND_SITES:
        wd = run_wind_days(wind_raw[site.key], alpha_used)
        wd["level"] = classify_with(wd["score"], th)
        all_wind[site.key] = wd
        cf_year = float(wd["daily_kwh"].sum()) / (WIND_CAPACITY_KW * 24 * len(wd))
        cf_old = float(wd["daily_kwh_old"].sum()) / (WIND_CAPACITY_KW * 24 * len(wd))
        dist = fmt_dist(level_dist(wd["level"]))
        wind_rows.append(
            f"| {site.name} | {site.zone} | {wd['v10_mean'].mean():.1f} | {wd['v100_mean'].mean():.1f} | "
            f"{cf_year:.2f} | {cf_old:.2f} | {wd['score'].mean():.0f} / {wd['score_old'].mean():.0f} | "
            f"{dist} |"
        )
        print(
            f"[{site.name}] 10m {wd['v10_mean'].mean():.1f} / 100m {wd['v100_mean'].mean():.1f} m/s，"
            f"CF 本模型 {cf_year:.2f} / 旧方法 {cf_old:.2f}，指数 {wd['score'].mean():.0f} / "
            f"{wd['score_old'].mean():.0f}，分档 {dist}"
        )

    pv_all = pd.concat(all_pv.values())
    wind_all = pd.concat(all_wind.values())
    monthly = pv_all.groupby(pv_all.index.month)["score"].mean()

    lines = [
        f"# 环境指数校准报告 {date.today()}\n",
        f"数据：Open-Meteo archive（ERA5）{start} ~ {end}，分档阈值 excellent/good/fair = {th}。",
        f"光伏交流 {PV_CAPACITY_KW:.0f} kW（容配比 {settings.pv_dc_ac_ratio}，直流 {kwp:.0f} kWp），"
        f"倾角=纬度、正南、系统损耗 {settings.pv_losses:.0%}、散射模型 {settings.pv_sky_diffuse_model}，"
        "太阳位置取小时区间中点；",
        f"风电 {WIND_CAPACITY_KW:.0f} kW，轮毂 {hub:.0f} m（10 m / 100 m 对数廓线插值），"
        f"场站损耗 {settings.wind_losses:.0%}，切入/额定/切出 "
        f"{settings.wind_v_in}/{settings.wind_v_rated}/{settings.wind_v_out} m/s。\n",
        "方法与判据见 [07 §八](../07-metrics.md)。脚本 `packages/server/scripts/calibrate.py`，"
        "改参数后重跑即可复现。\n",
        "## 1. 发电量准确度（光伏）\n",
        "| 站点 | 气候区 | 本模型年发电 kWh | PVGIS 年发电 kWh | 偏差 | 指数均值 | 分档 优/良/中/差 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *pv_rows,
        "\n本模型用单年 ERA5 再分析；PVGIS-ERA5 是 2005–2023 多年平均。判据 ±15%。\n",
        "## 2. 指数分布（光伏）\n",
        f"5 站合计 {len(pv_all)} 站·日，分档 优/良/中/差 = {fmt_dist(level_dist(pv_all['level']))}。",
        "分数分位："
        + "，".join(f"P{q} = {np.percentile(pv_all['score'], q):.0f}" for q in (10, 25, 50, 75, 90))
        + "\n",
        "按月均值（5 站平均）：\n",
        "| 月 | " + " | ".join(str(m) for m in monthly.index) + " |",
        "| --- |" + " --- |" * len(monthly),
        "| 指数 | " + " | ".join(f"{v:.0f}" for v in monthly.values) + " |\n",
        "## 3. 风电\n",
        f"本模型：轮毂风速由 10 m / 100 m 两层对数廓线插值，场站损耗 {settings.wind_losses:.0%}。"
        f"旧方法：10 m 按固定 α = {alpha_used} 幂律外推、无损耗（ERA5 拟合 α = {alpha_fit:.3f}，"
        "但昼夜差近 3 倍，固定值抹平了夜间大风）。\n",
        "| 站点 | 地形 | 10 m 年均 m/s | 100 m 年均 m/s | 年 CF 本模型 | 年 CF 旧方法 | "
        "指数均值 本/旧 | 分档 优/良/中/差 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *wind_rows,
        f"\n5 站合计分档 = {fmt_dist(level_dist(wind_all['level']))}。",
        "风电没有 PVGIS 这样的公开对账源。"
        "ERA5 25 km 网格抹平了山口与海岛的局地加速，复杂地形下偏低；"
        "实际风场年 CF 多在 0.22–0.35。\n",
        "## 4. 分档直觉抽查（夏半年）\n",
        "| 站点 | 类型 | 日期 | 日均云量 | 降水 | 日辐射 kWh/m² | 指数 | 分档 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *spot_rows,
    ]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"index-calibration-{date.today()}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pv_all.to_csv(DATA_DIR / "pv_daily.csv")
    wind_all.to_csv(DATA_DIR / "wind_daily.csv")
    print(f"\n报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
