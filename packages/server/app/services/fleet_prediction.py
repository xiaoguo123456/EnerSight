"""公共目录区域预测快照。单实例后台队列，原子落盘，失败场站不记为零。"""

import asyncio
import json
import logging
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.metrics import wind
from app.models import CatalogPlant, Station
from app.render import tiles
from app.schemas.prediction import FleetDay, FleetPrediction, PowerPoint, RegionPrediction
from app.services import prediction, weather
from app.services.prediction_basis import catalog_basis, version_for_day
from app.weather_model import MODELS

log = logging.getLogger(__name__)
_jobs: dict[str, asyncio.Task] = {}
_gate = asyncio.Lock()
TZ = "Asia/Shanghai"
FIELDS = [
    "wind_speed_10m",
    "wind_speed_80m",
    "wind_speed_100m",
    "wind_speed_120m",
    "temperature_2m",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
]


def directory() -> Path:
    path = tiles.tile_dir().parent / "fleet-predictions" / version_for_day(day_key())
    path.mkdir(parents=True, exist_ok=True)
    return path


def day_key() -> str:
    return datetime.now(ZoneInfo(TZ)).date().isoformat()


def write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False))
    tmp.replace(path)


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def days_count() -> int:
    return settings.forecast_outlook_days


def blank(model: str, day: str) -> FleetPrediction:
    return FleetPrediction(
        model=model,
        date=day,
        generated_at=datetime.now(UTC).isoformat(),
        energy_kwh=None,
        power_kw=[],
        status="queued",
        assumptions=[
            "区域近似：1°气象网格，按场站容量与能源类型估算",
            f"未来 {days_count()} 天同一批气象数据；第 4–7 天为中期预报，参考为主",
            f"已区分分期交直流容量，容配比假设 {settings.pv_dc_ac_ratio:g}、"
            f"系统损耗 {settings.pv_losses:.0%}（含逆变器）并按交流容量限幅；"
            "未知容量类型不计入预测",
            "采用默认设备参数，未计入限电、检修及故障影响",
            "统一北京时间；仅汇总平台运营目录，非全国实测电量",
            f"计算版本 {version_for_day(day)}",
            "逐时曲线按区间起点对齐光伏与风电"
            if version_for_day(day) == "model-v4"
            else "旧版逐时标签口径",
        ],
    )


def eligible(plants):
    seen, rows = set(), []
    duplicate, invalid = 0, 0
    for p in plants:
        if not (
            p.type in ("solar", "wind")
            and all(math.isfinite(v) for v in (p.latitude, p.longitude, p.capacity_kw))
            and p.capacity_kw > 0
            and -90 < p.latitude < 90
            and -180 <= p.longitude <= 180
        ):
            invalid += 1
            continue
        # 仅合并完全同名、同位置、同容量的重复记录，不误合并不同分期。
        key = (
            p.type,
            p.display_name.strip().casefold(),
            round(p.latitude, 5),
            round(p.longitude, 5),
            round(p.capacity_kw, 3),
        )
        if key in seen:
            duplicate += 1
            continue
        seen.add(key)
        rows.append(p)
    return rows, duplicate, invalid


def cell(p):
    return math.floor(p.latitude) + 0.5, math.floor(p.longitude) + 0.5


def calculate_cell(plants, raw, model, day):
    """一个 1° 网格内全部场站的未来 N 天单位容量曲线。返回 [(plant, [day0, day1, …])]，
    某天不可算时该位为 None；今日不可算的场站不计入覆盖。"""
    fc = weather.parse_forecast(raw, model=model)
    if fc.current_hour().date().isoformat() != day:
        raise ValueError("统计日期已变化")
    curves: dict[tuple, list[np.ndarray | None]] = {}
    results = []
    lat, lon = cell(plants[0])
    for p in plants:
        hub = wind.default_hub_height() if p.type == "wind" else None
        basis, blocked = catalog_basis(p)
        if blocked:
            continue
        dc_ratio, ac_ratio = (v / p.capacity_kw for v in basis)
        key = (p.type, hub, round(dc_ratio, 6), round(ac_ratio, 6))
        if key not in curves:
            st = Station(
                type=p.type,
                latitude=lat,
                longitude=lon,
                capacity_kw=1,
                tilt=None,
                azimuth=None,
                hub_height=hub,
            )
            st._pv_capacity = (dc_ratio, ac_ratio)
            per_day: list[np.ndarray | None] = []
            for k in range(days_count()):
                out = prediction.compute(st, fc, model, day_offset=k)
                per_day.append(
                    np.array([v.value for v in out.power_kw], dtype=float)
                    if out.energy_kwh is not None
                    else None
                )
            curves[key] = per_day
        per_day = curves[key]
        if per_day[0] is not None:
            results.append(
                (p, [c * p.capacity_kw if c is not None else None for c in per_day])
            )
    return results


async def build(http, model: str, day: str, plants) -> None:
    path = directory() / f"{model}-{day}.json"
    out = blank(model, day)
    rows, dup, invalid = eligible(plants)
    out.total_count = len(plants) - dup
    verified = [p for p in rows if not catalog_basis(p)[1]]
    out.eligible_count = len(verified)
    out.duplicate_count = dup
    out.invalid_count = invalid + len(rows) - len(verified)
    out.total_capacity_kw = sum(p.capacity_kw for p in rows) + sum(
        p.capacity_kw
        for p in plants
        if math.isfinite(p.capacity_kw)
        and p.capacity_kw > 0
        and (
            not math.isfinite(p.latitude)
            or not math.isfinite(p.longitude)
            or not -90 < p.latitude < 90
            or not -180 <= p.longitude <= 180
            or p.type not in ("solar", "wind")
        )
    )
    groups = defaultdict(list)
    for p in verified:
        groups[cell(p)].append(p)
    # 容量大的区域先计算，优先提高覆盖容量。
    keys = sorted(groups, key=lambda k: sum(p.capacity_kw for p in groups[k]), reverse=True)
    n_days = days_count()
    dates = [(date.fromisoformat(day) + timedelta(days=k)).isoformat() for k in range(n_days)]
    total = np.zeros((n_days, 24))
    solar_kwh = np.zeros(n_days)
    wind_kwh = np.zeros(n_days)
    covered_days = np.zeros(n_days, dtype=int)
    regions: list[dict[str, list]] = [{} for _ in range(n_days)]
    covered = set()
    # 起报与拉取时刻：批量请求没有逐格元数据，按模型取一次
    meta = await weather.get_model_meta(http, model)
    out.basis = weather.Forecast(tz=TZ, hourly=pd.DataFrame(), model=model, meta=meta).basis()
    cache_path = directory() / f"{model}-{day}-weather.json"
    saved = load(cache_path) or {}
    if saved.get("saved_at", 0) < datetime.now(UTC).timestamp() - 43200:
        saved = {}
    raw_cache = saved.get("cells", {})
    stop = False
    out.status = "building"

    def region_list(k: int) -> list[RegionPrediction]:
        return [
            RegionPrediction(province=name, energy_kwh=v[0], covered_count=v[1])
            for name, v in sorted(regions[k].items(), key=lambda item: item[1][0], reverse=True)
        ]

    def points(k: int) -> list[PowerPoint]:
        if not covered_days[k]:
            return []
        return [
            PowerPoint(time=f"{dates[k]}T{h:02d}:00:00+08:00", value=float(v))
            for h, v in enumerate(total[k])
        ]

    def publish():
        out.generated_at = datetime.now(UTC).isoformat()
        # 顶层字段始终是今日，与历史留档兼容；未来各天在 days 里。docs/17 §二
        out.energy_kwh = float(total[0].sum()) if out.covered_count else None
        out.solar_kwh = float(solar_kwh[0])
        out.wind_kwh = float(wind_kwh[0])
        out.power_kw = points(0)
        out.regions = region_list(0)
        out.days = [
            FleetDay(
                date=dates[k],
                weekday=date.fromisoformat(dates[k]).isoweekday(),
                lead_days=k,
                energy_kwh=float(total[k].sum()) if covered_days[k] else None,
                solar_kwh=float(solar_kwh[k]),
                wind_kwh=float(wind_kwh[k]),
                power_kw=points(k),
                regions=region_list(k),
            )
            for k in range(n_days)
        ]
        write(path, out.model_dump())

    publish()
    for start in range(0, len(keys), 25):
        if day != day_key():
            out.message = "统计日期已变化，请刷新"
            break
        batch = keys[start : start + 25]
        missing = [k for k in batch if f"{k[0]},{k[1]}" not in raw_cache]
        if missing:
            try:
                r = await http.get(
                    f"{settings.open_meteo_base}/forecast",
                    params={
                        "latitude": ",".join(str(k[0]) for k in missing),
                        "longitude": ",".join(str(k[1]) for k in missing),
                        "models": model,
                        "hourly": ",".join(FIELDS),
                        "timezone": TZ,
                        "forecast_days": n_days,
                        "wind_speed_unit": "ms",
                    },
                    timeout=40,
                )
                if r.status_code == 429:
                    out.message = "气象服务限流，已保存当前覆盖结果，稍后继续"
                    stop = True
                else:
                    r.raise_for_status()
                    payload = r.json()
                    payload = payload if isinstance(payload, list) else [payload]
                    if len(payload) != len(missing):
                        raise ValueError("批量响应数量不一致")
                    for key, raw in zip(missing, payload, strict=True):
                        raw_cache[f"{key[0]},{key[1]}"] = raw
                    write(
                        cache_path, {"saved_at": datetime.now(UTC).timestamp(), "cells": raw_cache}
                    )
            except Exception:
                log.warning("fleet weather batch failed: %s %s", model, start, exc_info=True)
                out.message = "部分区域气象数据暂不可用，显示已覆盖范围"
        for key in batch:
            raw = raw_cache.get(f"{key[0]},{key[1]}")
            if not raw:
                continue
            try:
                results = await asyncio.to_thread(calculate_cell, groups[key], raw, model, day)
            except Exception:
                log.warning("fleet cell failed: %s %s", model, key, exc_info=True)
                continue
            for p, per_day in results:
                if p.id in covered:
                    continue
                covered.add(p.id)
                out.covered_count += 1
                out.covered_capacity_kw += p.capacity_kw
                for k, curve in enumerate(per_day):
                    if curve is None:
                        continue
                    total[k] += curve
                    covered_days[k] += 1
                    energy = float(curve.sum())
                    if p.type == "solar":
                        solar_kwh[k] += energy
                    else:
                        wind_kwh[k] += energy
                    region = regions[k].setdefault(p.province or "地区待补充", [0.0, 0])
                    region[0] += energy
                    region[1] += 1
        publish()
        if stop:
            break
        await asyncio.sleep(0.4)
    out.failed_count = out.eligible_count - out.covered_count
    # 「算完了」与「覆盖了整个目录」是两件事。重复、字段非法、容量口径未核验的场站
    # 被主动排除，永远不会进入 covered，拿 total_count 判断会让状态永远停在 partial。
    # 覆盖程度由 covered_count / total_count 与 covered_capacity_kw / total_capacity_kw 表达。
    out.status = (
        "ready"
        if out.eligible_count and not out.failed_count
        else "partial"
        if out.covered_count
        else "error"
    )
    publish()
    from app.services import fleet_history

    fleet_history.capture(out.model_dump(), version_for_day(day))
    fleet_history.capture_leads(out.model_dump())
    # 留两天快照，清理旧天气文件，避免磁盘长期增长。
    for old in directory().glob("*.json"):
        if old.stat().st_mtime < datetime.now(UTC).timestamp() - 172800:
            old.unlink(missing_ok=True)


async def ensure(http, model: str) -> FleetPrediction:
    if model not in MODELS:
        raise ValueError("不支持的模型")
    day = day_key()
    key = f"{model}-{day}"
    path = directory() / f"{key}.json"
    saved = load(path)
    task = _jobs.get(key)
    if task and not task.done():
        return FleetPrediction.model_validate(saved) if saved else blank(model, day)
    if saved:
        age = (
            datetime.now(UTC).timestamp()
            - datetime.fromisoformat(saved["generated_at"]).timestamp()
        )
        if (saved["status"] == "ready" and age < 43200) or (
            saved["status"] in ("partial", "error") and age < 1800
        ):
            return FleetPrediction.model_validate(saved)
    initial = blank(model, day)
    write(path, initial.model_dump())

    async def run():
        async with _gate:
            try:
                async with SessionLocal() as db:
                    plants = (
                        (
                            await db.execute(
                                select(CatalogPlant).where(CatalogPlant.status == "operating")
                            )
                        )
                        .scalars()
                        .all()
                    )
                await build(http, model, day, plants)
            except Exception:
                log.exception("fleet prediction failed: %s", key)
                failed = load(path) or initial.model_dump()
                failed.update(
                    status="error",
                    message="汇总暂不可用，稍后自动重试",
                    generated_at=datetime.now(UTC).isoformat(),
                )
                write(path, failed)

    _jobs[key] = asyncio.create_task(run())
    for old in list(_jobs):
        if old != key and _jobs[old].done():
            del _jobs[old]
    return initial


async def shutdown():
    for task in _jobs.values():
        task.cancel()
    await asyncio.gather(*_jobs.values(), return_exceptions=True)
    _jobs.clear()
