"""卫星辐照精度回测：葵花 L2 SWR 与 PVOD 地面实测总辐照对账。docs/19 §四「校准」

问的是一件事：**卫星反演的辐照，比数值预报的辐照准多少。**

链路与线上完全一致 —— Buffalo 上的 Open-Meteo 下载器把历史帧按同样的扫描时刻校正入库，
这里按坐标查出来，不另写一份反演或校正。所以这个回测验的是线上那条路，不是实验室版本。

口径：
- 卫星是 10 分钟的区间均值标在区间末；PVOD 是 15 分钟采样，按小时平均后对账。
- 只看白天：晴空 GHI 大于 `MIN_CLEARSKY` 的小时。夜间与晨昏两头信噪比太低，
  收进来只会把相对误差稀释得好看。
- 基准是 PVOD 自带的 `nwp_globalirrad`（数值预报辐照）。两者用同一批小时、同一套统计，
  差值才有意义。

用法（需要先开到自建实例的通道）：
    uv run python scripts/validate_satellite.py --dates 2018-08-15,2019-01-15
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.metrics import solar  # noqa: E402
from scripts.reconcile_measured import TZ_CN, pvod_sites  # noqa: E402

DEFAULT_BASE = "http://127.0.0.1:8090/v1"
MODEL = "jma_jaxa_himawari"
# 晴空基准低于这个值的小时不进统计：晨昏两头的相对误差没有参考价值
MIN_CLEARSKY = 50.0
# 核心白天：太阳高的时段，出力基本都落在这里，单独再报一次
CORE_CLEARSKY = 200.0
# 逐站表里样本太少的行不出现：一两个晨昏小时算出来的百分比没有意义
MIN_STATION_HOURS = 5
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "satellite-validation"


def _parse(raw: dict) -> pd.Series:
    block = raw.get("hourly") or {}
    times = block.get("time") or []
    values = block.get("shortwave_radiation") or []
    if not times:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex(pd.to_datetime(times)).tz_localize("UTC").tz_convert(TZ_CN)
    return pd.Series([np.nan if v is None else float(v) for v in values], index=idx)


def fetch(
    http: httpx.Client | None,
    base: str,
    lat: float,
    lon: float,
    day: date,
    sid: str,
    from_dir: Path | None,
) -> pd.Series:
    """某站某天的卫星辐照，10 分钟一格，索引为电站当地时间。

    两个来源：Buffalo 上逐日取好的 JSON（`--from-dir`，回填完数据块会被清理，所以取值要当场存），
    或直接查自建实例（需要先开通道）。`temporal_resolution=native` 不能省 —— 不带只会回小时均值。
    某一天拿不到就跳过这一天，不要让整轮回测挂掉。
    """
    if from_dir is not None:
        path = from_dir / f"{day:%Y%m%d}_{sid}.json"
        if not path.exists():
            return pd.Series(dtype=float)
        return _parse(json.loads(path.read_text()))
    assert http is not None
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / f"{lat:.4f}_{lon:.4f}_{day}.json"
    if cache.exists():
        return _parse(json.loads(cache.read_text()))
    try:
        res = http.get(
            f"{base}/archive",
            params={
                "latitude": lat,
                "longitude": lon,
                "models": MODEL,
                "hourly": "shortwave_radiation",
                "temporal_resolution": "native",
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "timezone": "UTC",
            },
            timeout=120,
        )
        res.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"  跳过 {sid} {day}：{exc}", file=sys.stderr)
        return pd.Series(dtype=float)
    raw = res.json()
    cache.write_text(json.dumps(raw))
    return _parse(raw)


def hourly(series: pd.Series) -> pd.Series:
    """区间末标注的小时均值：(T-60, T] 内的点平均，**格数不齐就不要这一小时**。

    卫星一小时 6 格、实测 4 格。只判 NaN 不够 —— 缺帧时那一格根本不在序列里，
    count 与 size 都会少，照样算出「均值」。所以按步长反推应有格数，少一格就丢掉：
    晴空时段缺的那几格，用剩下的凑平均会把缺口抹平成正常值。
    """
    if series.empty:
        return series
    deltas = pd.Series(series.index).diff().dropna()
    step = deltas.median() if len(deltas) else pd.Timedelta(minutes=60)
    expected = max(1, int(round(pd.Timedelta(hours=1) / step)))
    grouped = series.groupby(series.index.ceil("h"))
    return grouped.mean().where(grouped.count() == expected).dropna()


def daytime_mask(lat: float, lon: float, index: pd.DatetimeIndex) -> pd.Series:
    """按晴空辐射判白天，不按图像也不按固定时段。"""
    cs = solar.clearsky_hourly_mean(lat, lon, TZ_CN, index)
    return pd.Series(cs["ghi"].to_numpy() > MIN_CLEARSKY, index=index)


def stats(model: pd.Series, measured: pd.Series) -> dict:
    """相对误差都以实测的白天均值为分母，不用逐点相对误差（小值会炸）。"""
    if len(model) == 0:
        return {"n": 0}
    base = float(measured.mean())
    err = model - measured
    return {
        "n": int(len(model)),
        "measured_mean": round(base, 1),
        "model_mean": round(float(model.mean()), 1),
        "bias_pct": round(float(err.mean()) / base * 100, 1),
        "mae_pct": round(float(err.abs().mean()) / base * 100, 1),
        "rmse_pct": round(float(np.sqrt((err**2).mean())) / base * 100, 1),
        "r": round(float(np.corrcoef(model, measured)[0, 1]), 3) if len(model) > 2 else None,
    }


def run(data_dir: Path, days: list[date], base: str, from_dir: Path | None = None) -> dict:
    rows: list[dict] = []
    pairs: list[pd.DataFrame] = []
    with httpx.Client() as http:
        for sid, _capacity, lat, lon, _tilt, df in pvod_sites(data_dir):
            frames = []
            for day in days:
                sat = hourly(fetch(http, base, lat, lon, day, sid, from_dir))
                if sat.empty:
                    continue
                meas = hourly(df["lmd_totalirrad"].astype(float))
                nwp = hourly(df["nwp_globalirrad"].astype(float))
                part = pd.DataFrame({"sat": sat, "meas": meas, "nwp": nwp}).dropna()
                if part.empty:
                    continue
                cs = solar.clearsky_hourly_mean(lat, lon, TZ_CN, part.index)["ghi"].to_numpy()
                part = part[cs > MIN_CLEARSKY]
                part["core"] = cs[cs > MIN_CLEARSKY] > CORE_CLEARSKY
                frames.append(part)
            if not frames:
                continue
            joined = pd.concat(frames)
            joined["station"] = sid
            pairs.append(joined)
            if len(joined) >= MIN_STATION_HOURS:
                rows.append(
                    {
                        "station": sid,
                        "satellite": stats(joined["sat"], joined["meas"]),
                        "nwp": stats(joined["nwp"], joined["meas"]),
                    }
                )
    allp = pd.concat(pairs) if pairs else pd.DataFrame(columns=["sat", "meas", "nwp", "core"])
    core = allp[allp["core"]] if len(allp) else allp
    return {
        "days": [d.isoformat() for d in days],
        "stations": rows,
        "overall": {
            "satellite": stats(allp["sat"], allp["meas"]),
            "nwp": stats(allp["nwp"], allp["meas"]),
        },
        "core": {
            "satellite": stats(core["sat"], core["meas"]),
            "nwp": stats(core["nwp"], core["meas"]),
        },
    }


def report(result: dict) -> str:
    o = result["overall"]
    lines = [
        "# 卫星辐照精度回测（葵花 L2 SWR × PVOD 地面实测）",
        "",
        f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"回测日期：{', '.join(result['days'])}",
        "",
        "口径见 [docs/19 §四](../19-forecast-uncertainty-and-observations.md)：",
        "卫星与实测都按「区间末标注的小时均值」对齐，只统计晴空基准大于 "
        f"{MIN_CLEARSKY:.0f} W/m² 的小时；相对误差以实测白天均值为分母。",
        "",
        "## 总体",
        "",
        "| 来源 | 样本 | 实测均值 | 模型均值 | 偏差 | 平均绝对误差 | 均方根误差 | 相关 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, name in (("satellite", "卫星（葵花 SWR）"), ("nwp", "数值预报（PVOD 自带）")):
        s = o[key]
        if not s.get("n"):
            continue
        lines.append(
            f"| {name} | {s['n']} | {s['measured_mean']} | {s['model_mean']} | "
            f"{s['bias_pct']:+.1f}% | {s['mae_pct']:.1f}% | {s['rmse_pct']:.1f}% | {s['r']} |"
        )
    lines += [
        "",
        f"只看核心白天（晴空基准 > {CORE_CLEARSKY:.0f} W/m²，出力主要落在这一段）：",
        "",
        "| 来源 | 样本 | 实测均值 | 模型均值 | 偏差 | 平均绝对误差 | 均方根误差 | 相关 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, name in (("satellite", "卫星（葵花 SWR）"), ("nwp", "数值预报（PVOD 自带）")):
        s = result["core"][key]
        if not s.get("n"):
            continue
        lines.append(
            f"| {name} | {s['n']} | {s['measured_mean']} | {s['model_mean']} | "
            f"{s['bias_pct']:+.1f}% | {s['mae_pct']:.1f}% | {s['rmse_pct']:.1f}% | {s['r']} |"
        )
    lines += [
        "",
        f"## 逐站（少于 {MIN_STATION_HOURS} 小时的站不列）",
        "",
        "| 电站 | 样本 | 卫星偏差 | 卫星 RMSE | 预报偏差 | 预报 RMSE |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in result["stations"]:
        s, n = r["satellite"], r["nwp"]
        lines.append(
            f"| {r['station']} | {s['n']} | {s['bias_pct']:+.1f}% | {s['rmse_pct']:.1f}% | "
            f"{n['bias_pct']:+.1f}% | {n['rmse_pct']:.1f}% |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data-dir", default=str(Path.home() / "Documents/project/enersight-validation-data")
    )
    ap.add_argument("--dates", required=True, help="逗号分隔，如 2018-08-15,2019-01-15")
    ap.add_argument("--base", default=DEFAULT_BASE, help="自建 Open-Meteo 的 /v1 地址")
    ap.add_argument("--out", default="", help="报告输出路径；不给只打印")
    ap.add_argument("--from-dir", default="", help="读 Buffalo 取好的取值 JSON，不再查接口")
    args = ap.parse_args()
    days = [date.fromisoformat(d.strip()) for d in args.dates.split(",") if d.strip()]
    result = run(
        Path(args.data_dir), days, args.base, Path(args.from_dir) if args.from_dir else None
    )
    text = report(result)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
