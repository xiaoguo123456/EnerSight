"""公共目录区域预测快照。单实例后台队列，原子落盘，失败场站不记为零。"""

import asyncio
import json
import logging
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import select

from app.db import SessionLocal
from app.metrics import wind
from app.models import CatalogPlant, Station
from app.render import tiles
from app.schemas.prediction import FleetPrediction, PowerPoint, RegionPrediction
from app.services import prediction, weather
from app.services.prediction_basis import VERSION, catalog_basis
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
    path = tiles.tile_dir().parent / "fleet-predictions" / VERSION
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
            "已区分分期交直流容量，容配比假设 1.2、系统损耗 14%（含逆变器）并按交流容量限幅；"
            "未知容量类型不计入预测",
            "采用默认设备参数，未计入限电、检修及故障影响",
            "统一北京时间；仅汇总平台运营目录，非全国实测电量",
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
    fc = weather.parse_forecast(raw)
    if fc.current_hour().date().isoformat() != day:
        raise ValueError("统计日期已变化")
    curves = {}
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
            out = prediction.compute(st, fc, model)
            curves[key] = (
                np.array([v.value for v in out.power_kw], dtype=float)
                if out.energy_kwh is not None
                else None
            )
        curve = curves[key]
        if curve is not None:
            results.append((p, curve * p.capacity_kw))
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
    total = np.zeros(24)
    regions = {}
    covered = set()
    cache_path = directory() / f"{model}-{day}-weather.json"
    saved = load(cache_path) or {}
    if saved.get("saved_at", 0) < datetime.now(UTC).timestamp() - 43200:
        saved = {}
    raw_cache = saved.get("cells", {})
    stop = False
    out.status = "building"

    def publish():
        out.generated_at = datetime.now(UTC).isoformat()
        out.energy_kwh = float(total.sum()) if out.covered_count else None
        out.power_kw = (
            [
                PowerPoint(time=f"{day}T{h:02d}:00:00+08:00", value=float(v))
                for h, v in enumerate(total)
            ]
            if out.covered_count
            else []
        )
        out.regions = [
            RegionPrediction(province=k, energy_kwh=v[0], covered_count=v[1])
            for k, v in sorted(regions.items(), key=lambda item: item[1][0], reverse=True)
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
                from app.config import settings

                r = await http.get(
                    f"{settings.open_meteo_base}/forecast",
                    params={
                        "latitude": ",".join(str(k[0]) for k in missing),
                        "longitude": ",".join(str(k[1]) for k in missing),
                        "models": model,
                        "hourly": ",".join(FIELDS),
                        "timezone": TZ,
                        "forecast_days": 1,
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
            for p, curve in results:
                if p.id in covered:
                    continue
                covered.add(p.id)
                total += curve
                out.covered_count += 1
                out.covered_capacity_kw += p.capacity_kw
                energy = float(curve.sum())
                if p.type == "solar":
                    out.solar_kwh += energy
                else:
                    out.wind_kwh += energy
                region = regions.setdefault(p.province or "地区待补充", [0.0, 0])
                region[0] += energy
                region[1] += 1
        publish()
        if stop:
            break
        await asyncio.sleep(0.4)
    out.failed_count = out.eligible_count - out.covered_count
    out.status = (
        "ready"
        if out.covered_count == out.total_count
        else "partial"
        if out.covered_count
        else "error"
    )
    publish()
    from app.services import fleet_history

    fleet_history.capture(out.model_dump(), VERSION)
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
        if (
            (
                saved["status"] == "ready"
                or (saved["status"] == "partial" and not saved.get("failed_count"))
            )
            and age < 43200
            or saved["status"] in ("partial", "error")
            and age < 1800
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
