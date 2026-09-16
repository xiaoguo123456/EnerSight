"""实测场站数据对账：模型比实测偏多少。docs/07 §八

与 calibrate.py 的分工：calibrate.py 对的是 PVGIS 多年平均理论值，这里对的是**场站实测功率**。

三个阶段，回答三个不同的问题：

  measured  用场站自己的实测气象驱动模型，排除气象预报误差，只看「气象 → 功率」的转换误差。
            光伏用实测总辐照与散射辐照，风电用实测轮毂高度风速、气温、气压。
  era5      用 Open-Meteo archive（ERA5）驱动同一条链路，得到全链路误差。
            与 measured 之差即气象输入误差。只有 PVOD 有坐标，能跑这一阶段。
  forecast  用 Open-Meteo **历史预报**接口驱动，得到线上真实用得到的预报精度。
            与 era5 的区别：那是再分析（事后最优估计），这里是当时真发出去的预报。

数据（下载与许可见 enersight-validation-data/README.md，不进仓库）：

  国网新能源发电功率预测竞赛（CC0）  6 座风电场，15 分钟，2019–2020，带轮毂高度实测风速。
      没有坐标，所以只做风电 —— 光伏要算太阳位置，无坐标就反推不出散射分量。
  PVOD v1.0（河北 10 座地面光伏）   15 分钟，2018-08 至 2019-06，带经纬度、容量、倾角、
      实测总辐照与散射辐照。时间戳是 UTC。
  香港科大 60 座屋顶光伏（CC0）      逐小时，2021-06 至 2023-12，带 Brick 元数据（容量、
      倾角、方位）与园区气象塔 1 分钟实测辐照。同一校园，共用一个气象格点。

两份场站数据都**没有限电与停机标记**，实测里混着限电、检修和故障，会让实测偏低、显得模型高估。

**时效说明**：Open-Meteo 的多时效存档（`_previous_dayN`，提前 N×24 小时的那一批）自 2024-01 起才有，
而香港科大数据截止 2023-12-31，两者不重叠。所以 forecast 阶段用的是历史预报接口的基础变量，
即每个时刻「当时最新一批运行」的预报，约 0–24 小时时效 —— 能代表次日预报，代表不了 7 天时效衰减。
要测时效衰减得换 2024 年以后的实测数据（AEMO、ONS、台电都在这个区间）。

用法：
  uv run python scripts/reconcile_measured.py                 # 三阶段都跑
  uv run python scripts/reconcile_measured.py --stage measured
  uv run python scripts/reconcile_measured.py --stage forecast
  uv run python scripts/reconcile_measured.py --data-dir <路径>

报告写到 docs/reports/measured-reconciliation-<日期>.md。
"""

from __future__ import annotations

import argparse
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


def daily_energy(power_kw: pd.Series) -> pd.Series:
    """15 分钟功率序列 → 逐日电量 kWh。缺测日不算。"""
    return power_kw.dropna().groupby(power_kw.dropna().index.date).sum() * (STEP / 60.0)


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


# ────────────────────────────── 香港科大：预报精度 ──────────────────────────────

HKUST_LAT, HKUST_LON = 22.3363, 114.2634
HKUST_TILT = 10.0  # 元数据里倾角是 0–15°，方位多为 "90/-90deg" 双朝向或 "Mixed"，
HKUST_AZIMUTH = 180.0  # 逐站建模不可行，按园区平均近似；倾角这么小，朝向影响有限
HKUST_EXCLUDE = {
    # 容量标注与实测明显对不上：峰值/容量分别是 788、42、9.0、1.9、0.37 倍
    "Tower A",
    "SQ9",
    "UG Hall4 Flexible PV",
    "SQ Apartment37-48 Flexible PV",
    "UG Hall7 Flexible PV",
}
HKUST_MIN_ONLINE = 0.5
"""在线容量占比低于这个数的时刻不参与对账。

60 座站起止时间差异极大（有的 2021-03 就有，有的 2023-08 才并网），任一时刻只有部分站在线。
所以口径取「实测出力 ÷ 当时在线容量」对「模型单位容量出力」，把在线站数的波动消掉。
"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def hkust_capacities(data_dir: Path) -> dict[str, float]:
    """Brick 元数据里的逐站额定容量（kW）。文件名与实体名拼写略有出入，按归一化匹配。"""
    ttl = (data_dir / "hkust-pv/Dataset/Metadata/PV generation system metadata.ttl").read_text(
        encoding="utf-8"
    )
    blocks = re.findall(
        r"pvsystem:(\w+) a brick:PV_Generation_System ;(.*?)(?=\n\npvsystem:|\Z)", ttl, re.S
    )
    caps = {}
    for name, body in blocks:
        m = re.search(
            r"ratedPowerOutput \[ brick:hasUnit unit:(\w+) ;\s*brick:value ([\d.]+)", body
        )
        if m:
            caps[_norm(name)] = float(m.group(2)) * (1000 if m.group(1).upper() == "MW" else 1)
    caps["indoorsportscentre"] = caps["indoorsportcenter"]  # 文件名拼写不同
    return caps


def hkust_campus_output(data_dir: Path) -> tuple[pd.Series, pd.Series, float]:
    """园区归一化出力、在线容量、纳入的总容量。

    60 座站里 37 座是 15 分钟、23 座是逐小时，先按站聚合到小时（区间末标签，与线上口径一致），
    否则时间轴对不齐。
    """
    caps = hkust_capacities(data_dir)
    root = data_dir / "hkust-pv/Dataset/Time series dataset/PV generation dataset"
    power, used = {}, {}
    for path in sorted(root.glob("**/Site level dataset/*.csv")):
        name = path.stem
        if name in HKUST_EXCLUDE:
            continue
        s = pd.read_csv(path, parse_dates=["Time"]).set_index("Time")["power(W)"].astype(float)
        s = s[~s.index.duplicated(keep="first")].sort_index() / 1000.0
        step = s.index.to_series().diff().mode()
        if len(step) and step[0] < pd.Timedelta("1h"):
            s = s.resample("1h", label="right", closed="right").mean()
        power[name] = s
        used[name] = caps[_norm(name)]
    frame = pd.DataFrame(power).sort_index()
    frame.index = pd.DatetimeIndex(frame.index).tz_localize(TZ_CN)  # 与预报侧对齐，香港同为 UTC+8
    cap = pd.Series(used)
    online = frame.notna().mul(cap, axis=1).sum(axis=1)
    out = frame.sum(axis=1, min_count=1)
    keep = online > cap.sum() * HKUST_MIN_ONLINE
    return (out[keep] / online[keep]), online[keep], float(cap.sum())


def hkust_irradiance(data_dir: Path) -> pd.Series:
    """园区气象塔实测总辐照，1 分钟 → 小时均值。

    辐照计有零点漂移（夜间读数约 11 W/m²），先按夜间中位数扣掉再聚合，否则实测系统性偏高。
    """
    root = data_dir / "hkust-pv/Dataset/Time series dataset/Meteorological dataset/Irradiance"
    parts = []
    for path in sorted(root.glob("Irradiance_*.csv")):
        d = pd.read_csv(path)
        d["Time"] = pd.to_datetime(d["Time"], format="%Y/%m/%d %H:%M")
        parts.append(d.set_index("Time")["Irradiance (W/m2)"].astype(float))
    obs = pd.concat(parts).sort_index()
    obs = obs[~obs.index.duplicated(keep="first")]
    obs.index = pd.DatetimeIndex(obs.index).tz_localize(TZ_CN)  # 与预报侧对齐
    night = float(obs.between_time("01:00", "04:00").median())
    return (obs - night).clip(lower=0).resample("1h", label="right", closed="right").mean()


def fetch_forecast(key: str, lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    """Open-Meteo **历史预报**：每个时刻当时最新一批运行的预报，不是事后再分析。

    用 curl 落盘缓存 —— 本机系统代理下 httpx 拉长区间偶发 502 与 TLS EOF（见 CLAUDE.md）。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{key}_{start}_{end}.json"
    if not cache.exists() or not cache.stat().st_size:
        url = (
            "https://historical-forecast-api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
            f"&hourly={','.join(ARCHIVE_VARS)}&timezone={TZ_CN}&wind_speed_unit=ms"
        )
        subprocess.run(
            ["curl", "-sS", "--retry", "5", "--retry-all-errors", "--retry-delay", "3",
             "-m", "240", "-o", str(cache), url],
            check=True,
        )  # fmt: skip
    h = json.loads(cache.read_text())["hourly"]
    # 必须带时区：PvInputs 声明 tz 但太阳位置按索引本身算，无时区索引会被当 UTC，
    # 整整错开 8 小时（实测过：峰值 0.39 vs 0.78，正午出力 0.001 vs 0.6）
    idx = pd.DatetimeIndex(pd.to_datetime(h["time"])).tz_localize(TZ_CN)
    return pd.DataFrame({k: v for k, v in h.items() if k != "time"}, index=idx).astype(float)


def run_hkust_forecast(data_dir: Path) -> tuple[list[Result], str]:
    """香港科大园区：预报驱动 vs 实测，并单独给出预报辐照与实测辐照的偏差。"""
    cf, online, cap_total = hkust_campus_output(data_dir)
    obs_ghi = hkust_irradiance(data_dir)
    fc = fetch_forecast(
        "hkust_forecast", HKUST_LAT, HKUST_LON, cf.index[0].date(), cf.index[-1].date()
    )

    # 单位容量（1 kW）等效站，得到单位容量出力，直接与归一化实测比
    model_unit = hourly_power(
        PvInputs(
            latitude=HKUST_LAT,
            longitude=HKUST_LON,
            tz=TZ_CN,
            capacity_kw=1.0,
            dc_capacity_kw=1.0,
            tilt=HKUST_TILT,
            azimuth=HKUST_AZIMUTH,
            times=fc.index,
            ghi=fc["shortwave_radiation"],
            dni=fc["direct_normal_irradiance"],
            dhi=fc["diffuse_radiation"],
            temp_air=fc["temperature_2m"],
            wind_speed=fc["wind_speed_10m"],
            step_minutes=60,
        )
    )

    rows = []
    both = pd.concat({"m": model_unit, "o": cf, "cap": online}, axis=1).dropna()
    for label, sub in (("全时段", both), ("仅白天（实测 CF>0.05）", both[both["o"] > 0.05])):
        if sub.empty:
            continue
        rows.append(
            Result(
                site="园区 55 座合计",
                capacity_kw=cap_total,
                days=sub.index.normalize().nunique(),
                measured_kwh=float((sub["o"] * sub["cap"]).sum()),
                model_kwh=float((sub["m"] * sub["cap"]).sum()),
                mae_kw=float(((sub["m"] - sub["o"]) * sub["cap"]).abs().mean()),
                note=f"历史预报驱动·{label}",
            )
        )

    # 预报辐照 vs 实测辐照：把「气象预报准不准」和「链路准不准」分开
    pair = pd.concat({"fc": fc["shortwave_radiation"], "obs": obs_ghi}, axis=1).dropna()
    day = pair[pair["obs"] > 20]
    daily = pair.resample("D").sum()
    daily = daily[daily["obs"] > 500]
    rel = ((daily["fc"] - daily["obs"]).abs() / daily["obs"]).mean()
    note = (
        f"预报辐照 / 实测辐照 = {day['fc'].sum() / day['obs'].sum():.3f}"
        f"（白天 {len(day)} 小时，偏差 {(day['fc'] - day['obs']).mean():+.1f} W/m²，"
        f"MAE {(day['fc'] - day['obs']).abs().mean():.1f} W/m²，相关 {day['fc'].corr(day['obs']):.3f}）；"
        f"逐日累计比值中位 {(daily['fc'] / daily['obs']).median():.3f}，"
        f"平均绝对相对误差 {rel:.0%}（{len(daily)} 天）"
    )
    return rows, note


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
        hk_rows, irr_note = run_hkust_forecast(args.data_dir)
        parts += [
            "\n## 四、光伏：香港科大园区，历史预报驱动（预报精度）\n\n",
            "前三节用的都是事后气象（站点实测或 ERA5 再分析），这一节换成**当时真发出去的预报**，"
            "才是线上用户实际拿到的精度。\n\n"
            f"**预报辐照本身**：{irr_note}。\n\n"
            "**时效**：Open-Meteo 的多时效存档（`_previous_dayN`）自 2024-01 才有，而这份数据截止 "
            "2023-12-31，两者不重叠。所以这里是「每个时刻当时最新一批运行」的预报，约 0–24 小时时效，"
            "**代表次日预报，不代表 7 天时效衰减**。\n\n"
            f"口径：60 座站排除 5 座容量标注明显有误的（峰值/容量 0.37–788 倍），余 55 座合计 "
            f"{hk_rows[0].capacity_kw / 1000:.2f} MW；各站起止差异大，按「实测出力 ÷ 当时在线容量」"
            f"对「模型单位容量出力」，只取在线容量过半的时刻。倾角按园区平均 {HKUST_TILT:g}°、正南近似"
            "（元数据里多为双朝向与 Mixed，逐站建模不可行）。屋顶分布式、同一个气象格点。\n\n",
            table(hk_rows),
        ]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"measured-reconciliation-{date.today()}.md"
    out.write_text("".join(parts), encoding="utf-8")
    print("".join(parts))
    print(f"\n报告写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
