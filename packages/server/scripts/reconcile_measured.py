"""实测场站数据对账：模型比实测偏多少。docs/07 §八

与 calibrate.py 的分工：calibrate.py 对的是 PVGIS 多年平均理论值，这里对的是**场站实测功率**。

三个阶段，回答三个不同的问题：

  measured  用场站自己的实测气象驱动模型，排除气象预报误差，只看「气象 → 功率」的转换误差。
            光伏用实测总辐照与散射辐照，风电用实测轮毂高度风速、气温、气压。
  era5      用 Open-Meteo archive（ERA5）驱动同一条链路，得到全链路误差。
            与 measured 之差即气象输入误差。只有 PVOD 有坐标，能跑这一阶段。
  forecast  用 Open-Meteo **历史预报**的多时效存档（`_previous_dayN`）驱动，看预报精度随
            时效怎么衰减 —— 这才是 7 天预测里用户实际拿到的东西。与 era5 的区别：
            那是再分析（事后最优估计），这里是提前 N 天真发出去的预报。

数据（下载与许可见 enersight-validation-data/README.md，不进仓库）：

  国网新能源发电功率预测竞赛（CC0）  6 座风电场，15 分钟，2019–2020，带轮毂高度实测风速。
      没有坐标，所以只做风电 —— 光伏要算太阳位置，无坐标就反推不出散射分量。
  PVOD v1.0（河北 10 座地面光伏）   15 分钟，2018-08 至 2019-06，带经纬度、容量、倾角、
      实测总辐照与散射辐照。时间戳是 UTC。
  AEMO NEMWEB（澳大利亚）           逐机组 5 分钟出力，2026-06 与 2026-07。
      `DISPATCHLOAD` 带 `UIGF`（无约束预测）与 `SEMIDISPATCHCAP`（出力被封顶），
      能把限电时段剔干净 —— 这是前两份中国数据做不到的。
      机组信息由 `DUDETAILSUMMARY` + `STATION` + `DUDETAIL` 三张注册表拼出
      （DUID → 电站名、类型、容量），坐标按电站名匹配 OpenStreetMap（ODbL，需署名）。

前两份中国数据**没有限电与停机标记**，实测里混着限电、检修和故障，会让实测偏低、显得模型高估。

**时效说明**：Open-Meteo 的多时效存档自 2024-01 起才有，所以只能配 2024 年以后的实测数据。
`_previous_day1` 是提前 24 小时那一批的预报，`_previous_day7` 是提前 168 小时，正好覆盖我们
7 天预测的全时效。NEM 结算时间是 AEST（UTC+10，不含夏令时），拉预报与对齐实测都用这个基准。

用法：
  uv run python scripts/reconcile_measured.py                 # 三阶段都跑
  uv run python scripts/reconcile_measured.py --stage measured
  uv run python scripts/reconcile_measured.py --stage forecast
  uv run python scripts/reconcile_measured.py --data-dir <路径>

报告写到 docs/reports/measured-reconciliation-<日期>.md。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.metrics import solar, wind  # noqa: E402
from app.metrics.pv import PvInputs, hourly_power  # noqa: E402
from app.services.prediction_basis import wind_turbine_class  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT.parents[1] / "docs" / "reports"
CACHE_DIR = ROOT / "data" / "reconcile"
DEFAULT_DATA = Path.home() / "Documents" / "project" / "enersight-validation-data"

TZ_CN = "Asia/Shanghai"
STEP = 15  # 两份数据都是 15 分钟
ARCHIVE_VARS = [
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "temperature_2m",
    "wind_speed_10m",
]


# ────────────────────────────── 通用 ──────────────────────────────


@dataclass
class Result:
    """一座场站的对账结果。电量单位 kWh，比值为 模型 / 实测。"""

    site: str
    capacity_kw: float
    days: int
    measured_kwh: float
    model_kwh: float
    mae_kw: float
    note: str = ""

    @property
    def ratio(self) -> float:
        return self.model_kwh / self.measured_kwh if self.measured_kwh > 0 else float("nan")

    @property
    def bias_pct(self) -> float:
        return (self.ratio - 1.0) * 100.0


def compare(
    site: str, capacity_kw: float, model_kw: pd.Series, measured_kw: pd.Series, note=""
) -> Result:
    """按共同的有效时刻对账，避免一边缺测导致电量口径不一致。"""
    both = pd.concat({"model": model_kw, "measured": measured_kw}, axis=1).dropna()
    if both.empty:
        return Result(site, capacity_kw, 0, 0.0, 0.0, 0.0, "无有效时刻")
    factor = STEP / 60.0
    return Result(
        site=site,
        capacity_kw=capacity_kw,
        days=both.index.normalize().nunique(),
        measured_kwh=float(both["measured"].sum()) * factor,
        model_kwh=float(both["model"].sum()) * factor,
        mae_kw=float((both["model"] - both["measured"]).abs().mean()),
        note=note,
    )


# ────────────────────────────── 国网竞赛：风电 ──────────────────────────────


def _col(df: pd.DataFrame, *must: str, exclude: str | None = None) -> str:
    """按关键词定位列。6 个风电场的表头有 4 种写法（空格与括号位置不一），硬编码必漏。

    轮毂那两列只差单位（风速 m/s 与风向 ˚），所以风速必须连 "m/s" 一起匹配。
    """
    for c in df.columns:
        low = str(c).lower()
        if all(k.lower() in low for k in must) and (exclude is None or exclude.lower() not in low):
            return str(c)
    raise KeyError(f"找不到同时含 {must} 的列：{list(df.columns)}")


def sgcc_wind_sites(data_dir: Path):
    """逐个风电场：容量由文件名里的 Nominal capacity 取，功率单位 MW。"""
    root = data_dir / "sgcc-competition" / "data_original" / "wind_farms"
    for path in sorted(root.glob("*.xlsx")):
        m = re.search(r"capacity-(\d+(?:\.\d+)?)MW", path.name)
        if not m:
            continue
        df = pd.read_excel(path)
        raw = df.iloc[:, 0].astype(str)
        # 国网用 24:00:00 表示当日末格，pandas 解析不了，先归一化成次日零点。
        # 这也说明它的时间戳是**区间末标签**，与我们「区间均值标在区间末」的口径一致。
        end_of_day = raw.str.endswith(" 24:00:00")
        idx = pd.to_datetime(raw.str.replace(" 24:00:00", " 00:00:00", regex=False))
        idx = idx + pd.to_timedelta(end_of_day.astype(int), unit="D")
        df = df.set_index(pd.DatetimeIndex(idx).tz_localize(TZ_CN))
        yield path.stem.split(" (")[0], float(m.group(1)) * 1000.0, df


def run_sgcc_wind(data_dir: Path, turbine_cls: str | None) -> list[Result]:
    """实测轮毂风速 + 气温气压 → 我们的功率曲线与密度修正，对实测出力。

    不经 energy.prepare：实测数据不需要缺测填补，也不需要多层风速插值。
    用的仍是线上同一套 wind.air_density / wind.plant_power。
    """
    out = []
    for name, capacity_kw, df in sgcc_wind_sites(data_dir):
        v_hub = df[_col(df, "wheel hub", "m/s")].astype(float)
        temp = df[_col(df, "temperature")].astype(float)
        press = df[_col(df, "atmosphere")].astype(float)
        measured = df[_col(df, "power")].astype(float).clip(lower=0) * 1000.0  # MW → kW
        rho = wind.air_density(press, temp, None)

        # 四档都跑：目录默认档（turbine_cls）只是其中之一，各场站真实机型不同，
        # 对照能看出「单一默认档」带来的分散有多大，也能看出哪档更接近实测。
        for cls in (turbine_cls or "generic", "generic", "medium_wind", "high_wind"):
            if (
                cls == "generic"
                and turbine_cls in (None, "generic")
                and out
                and out[-1].site == name
            ):
                continue  # 默认档就是通用档时不重复跑
            turbine = wind.Turbine(cls, None)
            model = wind.plant_power(v_hub, capacity_kw, rho=rho, turbine=turbine)
            label = wind.TURBINE_LABELS.get(cls, cls)
            mark = "（目录默认）" if cls == (turbine_cls or "generic") else ""
            out.append(compare(name, capacity_kw, model, measured, f"{label}{mark}"))
    return out


# ────────────────────────────── PVOD：光伏 ──────────────────────────────


def pvod_sites(data_dir: Path):
    """逐座光伏站：metadata 给经纬度、容量（kW）、倾角；时间戳是 UTC。"""
    root = data_dir / "pvod"
    meta = pd.read_csv(root / "metadata.csv").set_index("Station_ID")
    for sid, row in meta.iterrows():
        path = root / f"{sid}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        idx = pd.DatetimeIndex(pd.to_datetime(df["date_time"])).tz_localize("UTC")
        df = df.set_index(idx.tz_convert(TZ_CN)).drop(columns=["date_time"])
        # station00 有一个时刻重复了一行（2019-01-01 01:15），原始数据的瑕疵，留第一条
        df = df[~df.index.duplicated(keep="first")].sort_index()
        tilt = float(re.search(r"(\d+(?:\.\d+)?)", str(row["Array_Tilt"])).group(1))
        yield sid, float(row["Capacity"]), float(row["Latitude"]), float(row["Longitude"]), tilt, df


def pv_model_power(
    *,
    lat: float,
    lon: float,
    tilt: float,
    capacity_kw: float,
    dc_capacity_kw: float,
    ghi: pd.Series,
    dhi: pd.Series,
    temp: pd.Series,
    wind_speed: pd.Series,
    shift_end: bool,
) -> pd.Series:
    """由实测总辐照与散射辐照跑线上光伏链路。

    我们的口径是「区间均值标在区间末」，而两份数据都没写明时间戳是区间起点还是末点，
    所以 shift_end 两种假设都跑一遍，报告里取误差小的并注明。

    DNI 由 GHI、DHI 和区间中点天顶角反算：DNI = (GHI − DHI) / cos z。
    天顶角接近 90° 时该式发散，按太阳高度角 5° 以下置 0。
    """
    times = ghi.index + pd.Timedelta(minutes=STEP) if shift_end else ghi.index
    ghi = pd.Series(ghi.to_numpy(), index=times).clip(lower=0)
    dhi = pd.Series(dhi.to_numpy(), index=times).clip(lower=0)
    temp = pd.Series(temp.to_numpy(), index=times)
    wind_speed = pd.Series(wind_speed.to_numpy(), index=times)

    pos = solar.solar_position_interval(lat, lon, TZ_CN, times, STEP)
    cos_z = np.cos(np.radians(pos["zenith"].to_numpy()))
    dni = (ghi - dhi.clip(upper=ghi)) / np.where(cos_z > np.sin(np.radians(5.0)), cos_z, np.nan)
    dni = pd.Series(dni, index=times).fillna(0.0).clip(lower=0, upper=1200)

    return hourly_power(
        PvInputs(
            latitude=lat,
            longitude=lon,
            tz=TZ_CN,
            capacity_kw=capacity_kw,
            dc_capacity_kw=dc_capacity_kw,
            tilt=tilt,
            azimuth=180.0,  # metadata 写的都是朝南
            times=times,
            ghi=ghi,
            dni=dni,
            dhi=dhi,
            temp_air=temp,
            wind_speed=wind_speed,
            step_minutes=STEP,
        )
    )


SUSPECT_CF = 0.65
"""高辐照（GHI>600）时实测出力不到容量 65% 的站，判为受限或衰减，不进主结论。

正常地面电站此时应接近满发：PVOD 里 station04 是 0.86、station01 0.76、station05 0.72。
低的一批是 station09 0.26、station06 0.50、station07 0.56、station00 0.57、station08 0.62，
而它们白天出力为 0 的时刻占比都是 0 —— 不是停机，是被长期压低出力，或者功率只记了部分方阵。
这类站的偏差不该算在模型头上，另列一组。
"""


def limited_ratio(measured_kw: pd.Series, ghi: pd.Series, capacity_kw: float) -> float:
    """高辐照时段的实测出力 / 容量中位数，用来判断这座站是不是长期受限。"""
    high = measured_kw[ghi > 600.0]
    return float((high / capacity_kw).median()) if len(high) else float("nan")


def run_pvod_measured(data_dir: Path) -> list[Result]:
    """PVOD 的 Capacity 是**直流侧**组件容量：metadata 里组件数 × 单块 Pmax 与它几乎相等
    （比值 0.999–1.034）。所以直流容量直接给 Capacity，不能再让链路按容配比放大 1.2 倍；
    交流侧未知，按同值传入，相当于容配比 1.0，是偏保守的假设。
    """
    out = []
    for sid, capacity_kw, lat, lon, tilt, df in pvod_sites(data_dir):
        measured = df["power"].astype(float).clip(lower=0)
        if measured.max() < capacity_kw * 0.1:  # power 列是 MW，容量是 kW
            measured = measured * 1000.0
        ghi_raw = df["lmd_totalirrad"].astype(float)
        cf = limited_ratio(measured, ghi_raw, capacity_kw)
        suspect = "·疑似长期受限" if cf < SUSPECT_CF else ""

        best_row: Result | None = None
        best_model: pd.Series | None = None
        for shift_end in (False, True):
            model = pv_model_power(
                lat=lat,
                lon=lon,
                tilt=tilt,
                capacity_kw=capacity_kw,
                dc_capacity_kw=capacity_kw,
                ghi=ghi_raw,
                dhi=df["lmd_diffuseirrad"].astype(float),
                temp=df["lmd_temperature"].astype(float),
                wind_speed=df["lmd_windspeed"].astype(float),
                shift_end=shift_end,
            )
            tag = "区间末标签" if shift_end else "区间起点标签"
            r = compare(sid, capacity_kw, model, measured, f"{tag}（高辐照CF {cf:.2f}）{suspect}")
            if best_row is None or abs(r.bias_pct) < abs(best_row.bias_pct):
                best_row, best_model = r, model
        assert best_row is not None and best_model is not None
        model = best_model
        out.append(best_row)

        # 只看高辐照时段：这段最能反映转换误差，也最不容易被弱光与限电污染。
        # 原先「剔除实测为 0 的时刻」对光伏完全无效 —— 白天零值占比本就是 0。
        ghi = pd.Series(ghi_raw.to_numpy(), index=model.index)
        obs = pd.Series(measured.to_numpy(), index=model.index)
        keep = ghi > 600.0
        out.append(compare(sid, capacity_kw, model[keep], obs[keep], f"仅高辐照 GHI>600{suspect}"))
    return out


# ────────────────────────────── ERA5 全链路 ──────────────────────────────


def fetch_archive(http: httpx.Client, key: str, lat: float, lon: float, start: date, end: date):
    """Open-Meteo archive（ERA5）逐小时，缓存原始响应。与 calibrate.py 同口径。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"archive_{key}_{start}_{end}.json"
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        res = http.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hourly": ",".join(ARCHIVE_VARS),
                "timezone": TZ_CN,
                "wind_speed_unit": "ms",
            },
            timeout=120,
        )
        res.raise_for_status()
        raw = res.json()
        cache.write_text(json.dumps(raw))
    h = raw["hourly"]
    idx = pd.DatetimeIndex(pd.to_datetime(h["time"])).tz_localize(TZ_CN)
    return pd.DataFrame({k: v for k, v in h.items() if k != "time"}, index=idx).astype(float)


def run_pvod_era5(data_dir: Path) -> list[Result]:
    """同样的链路换 ERA5 驱动：与 measured 阶段之差即气象输入误差。

    ERA5 是逐小时，实测是 15 分钟，按小时聚合后对账。
    """
    out = []
    with httpx.Client() as http:
        for sid, capacity_kw, lat, lon, tilt, df in pvod_sites(data_dir):
            measured = df["power"].astype(float).clip(lower=0)
            if measured.max() < capacity_kw * 0.1:
                measured = measured * 1000.0
            # 与 measured 阶段同口径：直流容量直接给 Capacity，不再乘容配比；
            # 受限站同样单独标记。两阶段口径必须一致，否则相减得不到气象输入误差。
            cf = limited_ratio(measured, df["lmd_totalirrad"].astype(float), capacity_kw)
            suspect = "·受限或衰减" if cf < SUSPECT_CF else ""
            start, end = df.index[0].date(), df.index[-1].date()
            era = fetch_archive(http, sid, lat, lon, start, end)
            model = hourly_power(
                PvInputs(
                    latitude=lat,
                    longitude=lon,
                    tz=TZ_CN,
                    capacity_kw=capacity_kw,
                    dc_capacity_kw=capacity_kw,
                    tilt=tilt,
                    azimuth=180.0,
                    times=era.index,
                    ghi=era["shortwave_radiation"],
                    dni=era["direct_normal_irradiance"],
                    dhi=era["diffuse_radiation"],
                    temp_air=era["temperature_2m"],
                    wind_speed=era["wind_speed_10m"],
                    step_minutes=60,
                )
            )
            hourly_measured = measured.resample("1h", label="right", closed="right").mean()
            both = pd.concat({"m": model, "o": hourly_measured}, axis=1).dropna()
            if both.empty:
                continue
            out.append(
                Result(
                    site=sid,
                    capacity_kw=capacity_kw,
                    days=both.index.normalize().nunique(),
                    measured_kwh=float(both["o"].sum()),
                    model_kwh=float(both["m"].sum()),
                    mae_kw=float((both["m"] - both["o"]).abs().mean()),
                    note=f"ERA5 驱动·逐小时{suspect}",
                )
            )
    return out


# ────────────────────────────── 报告 ──────────────────────────────


def table(rows: list[Result]) -> str:
    head = (
        "| 场站 | 容量 MW | 天数 | 实测 MWh | 模型 MWh | 模型/实测 | 偏差 | MAE kW | 口径 |\n"
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |\n"
    )
    body = "".join(
        f"| {r.site} | {r.capacity_kw / 1000:.0f} | {r.days} | {r.measured_kwh / 1000:,.0f} | "
        f"{r.model_kwh / 1000:,.0f} | {r.ratio:.2f} | {r.bias_pct:+.0f}% | {r.mae_kw:,.0f} | {r.note} |\n"
        for r in rows
    )
    return head + body


def overall(rows: list[Result]) -> str:
    if not rows:
        return "无数据"
    ratios = [r.ratio for r in rows if np.isfinite(r.ratio)]
    total_m = sum(r.model_kwh for r in rows)
    total_o = sum(r.measured_kwh for r in rows)
    return (
        f"合计模型/实测 {total_m / total_o:.2f}（{(total_m / total_o - 1) * 100:+.0f}%），"
        f"逐站比值中位数 {np.median(ratios):.2f}，范围 {min(ratios):.2f}–{max(ratios):.2f}"
    )


# ────────────────────────────── AEMO：预报时效衰减 ──────────────────────────────

TZ_AU = "Australia/Brisbane"
"""NEM 结算时间是 AEST（UTC+10，全年不含夏令时），Brisbane 正好是这个偏移。"""

AEMO_MONTHS = ("2026_06", "2026_07")
AEMO_TOP_N = 12  # 按容量取前 N 座风电场；再多就是拉气象的钱和时间，趋势已经稳定
AEMO_LEADS = (0, 1, 2, 3, 4, 5, 6, 7)  # 0 = 基础变量（当时最新一批），1–7 = 提前 N×24 小时
AEMO_VARS = (
    "wind_speed_10m",
    "wind_speed_80m",
    "wind_speed_100m",
    "wind_speed_120m",
    "temperature_2m",
    "surface_pressure",
)


def aemo_units(data_dir: Path, top_n: int = AEMO_TOP_N) -> dict[str, dict]:
    """按容量取前 N 座风电场。

    `duid_located.json` 由三张注册表拼出：`DUDETAILSUMMARY` 给 DUID → STATIONID 与
    SCHEDULE_TYPE（SEMI-SCHEDULED 即风光），`STATION` 给电站名，`DUDETAIL` 给容量；
    坐标按电站名匹配 OpenStreetMap 的 `power=plant`（ODbL，需署名）。
    """
    located = json.loads((data_dir / "aemo" / "duid_located.json").read_text(encoding="utf-8"))
    wind = {k: v for k, v in located.items() if v.get("kind") == "wind" and v.get("capacity_mw")}
    top = sorted(wind, key=lambda k: -wind[k]["capacity_mw"])[:top_n]
    return {k: wind[k] for k in top}


def _aemo_stream(path: Path, table: str):
    """MMSDM 归档是 zip 里一个大 CSV，按 I 行定表头、D 行出数据。文件动辄几百 MB，流式读。"""
    import zipfile

    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
        header = None
        for row in csv.reader(text):
            if not row:
                continue
            if row[0] == "I" and row[2] == table:
                header = {k: i for i, k in enumerate(row[4:])}
            elif row[0] == "D" and row[2] == table and header:
                yield row[4:], header


def aemo_measured(data_dir: Path, duids: set[str]) -> pd.DataFrame:
    """逐机组 5 分钟出力 → 小时均值（MW）。

    SCADAVALUE 是时段起点瞬时值，SETTLEMENTDATE 标在调度间隔末，聚合按区间末口径。
    """
    frames = []
    for month in AEMO_MONTHS:
        path = data_dir / "aemo" / f"{month}_DISPATCH_UNIT_SCADA.zip"
        rec: dict[str, list[tuple[str, float]]] = {d: [] for d in duids}
        for row, h in _aemo_stream(path, "UNIT_SCADA"):
            duid = row[h["DUID"]]
            if duid in rec:
                rec[duid].append((row[h["SETTLEMENTDATE"]], float(row[h["SCADAVALUE"]] or 0)))
        for duid, vals in rec.items():
            if not vals:
                continue
            s = pd.Series(
                [v for _, v in vals],
                index=pd.DatetimeIndex(pd.to_datetime([t for t, _ in vals])).tz_localize(TZ_AU),
            ).sort_index()
            frames.append(
                s.resample("1h", label="right", closed="right").mean().rename(duid).to_frame()
            )
    return pd.concat(frames).groupby(level=0).first() if frames else pd.DataFrame()


def aemo_capped_hours(data_dir: Path, duids: set[str]) -> dict[str, pd.DatetimeIndex]:
    """被限电的小时：该小时内任一 5 分钟 `SEMIDISPATCHCAP=1` 就整小时剔除。

    半调度机组被封顶时，出力是调度指令而非气象决定的，留着会把限电算成模型误差。
    只看 `INTERVENTION=0` 的正常运行解。
    """
    capped: dict[str, set] = {d: set() for d in duids}
    for month in AEMO_MONTHS:
        path = data_dir / "aemo" / f"{month}_DISPATCHLOAD.zip"
        for row, h in _aemo_stream(path, "UNIT_SOLUTION"):
            duid = row[h["DUID"]]
            if duid not in capped or row[h["INTERVENTION"]] != "0":
                continue
            if row[h["SEMIDISPATCHCAP"]] == "1":
                capped[duid].add(pd.Timestamp(row[h["SETTLEMENTDATE"]]).ceil("1h"))
    return {
        d: pd.DatetimeIndex(sorted(v)).tz_localize(TZ_AU) if v else pd.DatetimeIndex([])
        for d, v in capped.items()
    }


def _fetch_json(key: str, url: str) -> dict:
    """拉一次并落盘缓存。

    用 curl 不用 httpx：本机系统代理下拉长区间偶发 502 与 TLS EOF，见 CLAUDE.md「已知的环境坑」。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{key}.json"
    for attempt in range(4):
        if not cache.exists() or not cache.stat().st_size:
            subprocess.run(
                ["curl", "-sS", "--retry", "5", "--retry-all-errors", "--retry-delay", "3",
                 "-m", "300", "-o", str(cache), url],
                check=True,
            )  # fmt: skip
        try:
            data = json.loads(cache.read_text())
        except ValueError:
            # 上游偶发返回 "Unexpected error while streaming data: timeoutReached" 这类纯文本，
            # curl 不会当成失败，坏内容落盘后会被后续运行反复命中，所以解析失败就删缓存重来
            body = cache.read_text(errors="replace")[:200]
            cache.unlink(missing_ok=True)
            if attempt == 3:
                raise RuntimeError(f"{key} 拉取失败，上游返回：{body}") from None
            continue
        if "error" in data:
            cache.unlink(missing_ok=True)
            raise RuntimeError(f"{key} 拉取失败：{data.get('reason')}")
        return data
    raise RuntimeError(f"{key} 拉取失败")


def fetch_leads(
    key: str, lat: float, lon: float, start: date, end: date
) -> dict[int, pd.DataFrame]:
    """一次请求把 0–7 天时效的六个变量都拉回来，按时效拆成多张表。"""
    names = list(AEMO_VARS) + [
        f"{v}_previous_day{lead}" for lead in AEMO_LEADS if lead for v in AEMO_VARS
    ]
    raw = _fetch_json(
        f"lead_{key}",
        "https://historical-forecast-api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
        f"&hourly={','.join(names)}&timezone={TZ_AU}&wind_speed_unit=ms",
    )
    h = raw["hourly"]
    idx = pd.DatetimeIndex(pd.to_datetime(h["time"])).tz_localize(TZ_AU)
    out = {}
    for lead in AEMO_LEADS:
        suffix = "" if lead == 0 else f"_previous_day{lead}"
        cols = {v: h.get(f"{v}{suffix}") for v in AEMO_VARS}
        if any(c is None for c in cols.values()):
            continue
        out[lead] = pd.DataFrame(cols, index=idx).astype(float)
    return out


def run_aemo_leadtime(data_dir: Path) -> list[Result]:
    """预报精度随时效的衰减：同一批场站、同一条链路，只换预报时效。"""
    units = aemo_units(data_dir)
    measured = aemo_measured(data_dir, set(units))
    capped = aemo_capped_hours(data_dir, set(units))
    hub = wind.default_hub_height()
    turbine = wind.Turbine(wind_turbine_class(None, offshore=False) or "generic", None)

    acc: dict[int, list[pd.DataFrame]] = {lead: [] for lead in AEMO_LEADS}
    raw_acc: dict[int, list[pd.DataFrame]] = {lead: [] for lead in AEMO_LEADS}
    for duid, info in units.items():
        if duid not in measured:
            continue
        obs = measured[duid].dropna()
        if obs.empty:
            continue
        leads = fetch_leads(
            duid, info["lat"], info["lon"], obs.index[0].date(), obs.index[-1].date()
        )
        capacity_kw = info["capacity_mw"] * 1000.0
        for lead, fc in leads.items():
            levels = {
                float(h[len("wind_speed_") : -1]): fc[h] for h in AEMO_VARS if "wind_speed" in h
            }
            v_hub, _ = wind.hub_wind_speed(levels, hub)
            rho = wind.air_density(fc["surface_pressure"], fc["temperature_2m"], None)
            model_mw = wind.plant_power(v_hub, capacity_kw, rho=rho, turbine=turbine) / 1000.0
            pair = pd.concat({"m": model_mw, "o": obs}, axis=1).dropna()
            if pair.empty:
                continue
            raw_acc[lead].append(pair)
            acc[lead].append(
                pair.drop(index=capped.get(duid, pd.DatetimeIndex([])), errors="ignore")
            )

    rows = []
    for lead in AEMO_LEADS:
        for label, store in (("剔除限电时段", acc), ("含限电时段", raw_acc)):
            parts = [p for p in store[lead] if not p.empty]
            if not parts:
                continue
            all_pairs = pd.concat(parts)
            lead_txt = "当时最新批次（约 0–24 h）" if lead == 0 else f"提前 {lead} 天"
            rows.append(
                Result(
                    site=f"{len(parts)} 座风电场",
                    capacity_kw=sum(u["capacity_mw"] for u in units.values()) * 1000.0,
                    days=all_pairs.index.normalize().nunique(),
                    measured_kwh=float(all_pairs["o"].sum()) * 1000.0,
                    model_kwh=float(all_pairs["m"].sum()) * 1000.0,
                    mae_kw=float((all_pairs["m"] - all_pairs["o"]).abs().mean()) * 1000.0,
                    note=f"{lead_txt}·{label}"
                    + f"（相关 {all_pairs['m'].corr(all_pairs['o']):.3f}）",
                )
            )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("measured", "era5", "forecast", "all"), default="all")
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    args = ap.parse_args()

    if not args.data_dir.exists():
        print(f"数据目录不存在：{args.data_dir}")
        return 1

    parts = [f"# 实测场站对账（{date.today()}）\n"]
    parts.append(
        "模型用的是线上同一套实现（`metrics/pv.hourly_power`、`metrics/wind.plant_power`），"
        f"参数为线上默认：光伏系统损耗 {settings.pv_losses:.0%}、"
        f"风电场站损耗 {settings.wind_losses:.0%}、参考空气密度 {settings.wind_air_density_ref:g} kg/m³。\n\n"
        f"**容配比例外**：线上默认 {settings.pv_dc_ac_ratio:g}，但 PVOD 的 `Capacity` 本身就是直流侧"
        "组件容量（metadata 里组件数 × 单块 Pmax 与它比值 0.999–1.034），所以这里直接按直流容量传入，"
        "不再乘容配比；交流侧未知，按同值传入，相当于容配比 1.0。\n\n"
        "两份数据都没有限电与停机标记，实测里混着限电、检修和故障，会让实测偏低、显得模型高估。"
        "所以风电按四档机型各跑一遍作对照，光伏给「全时段」与「仅高辐照 GHI>600」两个口径 —— "
        "后者最能反映转换误差，也最不容易被弱光与限电污染。\n"
    )

    if args.stage in ("measured", "all"):
        cls = wind_turbine_class(None, offshore=False)
        wind_rows = run_sgcc_wind(args.data_dir, cls)
        pv_rows = run_pvod_measured(args.data_dir)
        default_rows = [r for r in wind_rows if "目录默认" in r.note]
        pv_main = [r for r in pv_rows if "仅高辐照" in r.note and "受限" not in r.note]
        pv_suspect = [r for r in pv_rows if "仅高辐照" in r.note and "受限" in r.note]
        parts += [
            "\n## 一、风电：国网竞赛 6 座风电场（实测轮毂风速驱动）\n\n",
            f"目录默认档 `{cls or 'generic'}`：{overall(default_rows)}\n\n"
            "四档都列出来，是为了看「单一默认档」带来的分散有多大。\n\n",
            table(wind_rows),
            "\n## 二、光伏：PVOD 10 座地面光伏（实测辐照驱动）\n\n",
            f"主结论（排除受限或衰减的站，仅高辐照时段）：{overall(pv_main)}\n\n"
            f"受限或衰减的站另算：{overall(pv_suspect)}。判据是 GHI>600 时实测出力不到容量 "
            f"{SUSPECT_CF:.0%}（station09 只有 0.26，另有四座 0.50–0.62；正常站 0.72–0.86）。"
            "这些站白天出力为 0 的时刻占比都是 0，不是停机，是被长期压低出力或只记了部分方阵，"
            "它们的偏差不该算在模型头上。\n\n",
            table(pv_rows),
        ]

    if args.stage in ("era5", "all"):
        era_rows = run_pvod_era5(args.data_dir)
        era_main = [r for r in era_rows if "受限" not in r.note]
        era_suspect = [r for r in era_rows if "受限" in r.note]
        parts += [
            "\n## 三、光伏：PVOD 换 ERA5 驱动（全链路）\n\n",
            f"主结论（同样排除受限或衰减的站）：{overall(era_main)}\n\n"
            f"受限或衰减的站另算：{overall(era_suspect)}\n\n"
            "与第二节的差即**气象输入误差**：两节容量口径、站点范围一致，差别只在气象来自"
            "站点实测还是 ERA5。第二节按 15 分钟、第三节按逐小时聚合，因此时段口径略有不同。\n\n",
            table(era_rows),
        ]

    if args.stage in ("forecast", "all"):
        lead_rows = run_aemo_leadtime(args.data_dir)
        clean = [r for r in lead_rows if "剔除限电" in r.note]
        parts += [
            "\n## 四、风电：AEMO 预报时效衰减\n\n",
            "前三节用的都是事后气象（站点实测或 ERA5 再分析），这一节换成**当时真发出去的预报**，"
            "并按时效拆开：`_previous_day1` 是提前 24 小时那一批，`_previous_day7` 是提前 168 小时，"
            "正好覆盖我们 7 天预测的全时效。同一批场站、同一条链路，只换预报时效。\n\n"
            f"口径：按容量取前 {AEMO_TOP_N} 座风电场（{clean[0].capacity_kw / 1e6:.1f} GW），"
            f"{'、'.join(AEMO_MONTHS).replace('_', '-')} 两个月，逐机组 5 分钟出力聚合到小时。"
            "风速按 10/80/100/120 m 四层对数廓线插值到轮毂高度，与线上同口径。\n\n"
            "**限电剔除**：半调度机组被封顶（`SEMIDISPATCHCAP=1`）时出力由调度指令决定，不是气象决定的，"
            "留着会把限电算成模型误差。该小时内任一 5 分钟被封顶就整小时剔除；「含限电时段」一行作对照。\n\n"
            "机组信息由 AEMO 三张注册表拼出，坐标按电站名匹配 OpenStreetMap（ODbL，需署名）。\n\n",
            table(lead_rows),
        ]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"measured-reconciliation-{date.today()}.md"
    out.write_text("".join(parts), encoding="utf-8")
    print("".join(parts))
    print(f"\n报告写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
