"""公共目录区域预测快照。单实例后台队列，原子落盘，失败场站不记为零。"""

import asyncio
import json
import logging
import math
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.metrics import wind
from app.models import CatalogPlant, Station
from app.providers.budget import shared
from app.providers.weather_transport import weather_get
from app.render import tiles
from app.schemas.prediction import (
    FleetDay,
    FleetPrediction,
    ForecastBasis,
    PowerPoint,
    RegionPrediction,
)
from app.services import energy, weather
from app.services.curve_cache import CurveCache
from app.services.prediction_basis import (
    calculation_version,
    catalog_basis,
    catalog_cell_selection,
    catalog_turbine_class,
    version_for_day,
)
from app.weather_model import MODELS

log = logging.getLogger(__name__)
_jobs: dict[str, asyncio.Task] = {}
_checked: dict[str, float] = {}
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
    "surface_pressure",
]


class Pacer:
    """按坐标数限速的漏桶。

    Open-Meteo 的 600 次/分钟是**按坐标计**的：实测一分钟内推到第 600 个坐标就返回
    429，与分成几次请求无关。多坐标请求只省 HTTP 往返，不省额度。限额还按 IP 算，
    站点预报与元数据共用同一份，所以这里留两成余量。
    """

    def __init__(self, per_minute: int) -> None:
        self._cost = 60.0 / max(1, per_minute)
        self._next = 0.0

    async def take(self, n: int) -> None:
        now = time.monotonic()
        if self._next > now:
            await asyncio.sleep(self._next - now)
        self._next = max(now, self._next) + n * self._cost


def directory() -> Path:
    path = tiles.tile_dir().parent / "fleet-predictions" / version_for_day(day_key())
    path.mkdir(parents=True, exist_ok=True)
    return path


def beijing_now() -> datetime:
    return datetime.now(ZoneInfo(TZ))


def day_key() -> str:
    return beijing_now().date().isoformat()


def write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False)
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
        calculation_version=calculation_version(day),
        model=model,
        date=day,
        generated_at=datetime.now(UTC).isoformat(),
        energy_kwh=None,
        power_kw=[],
        status="queued",
        resolution_minutes=15,
        assumptions=[
            f"区域近似：光伏 {settings.fleet_grid_step_solar:g}°、"
            f"风电 {settings.fleet_grid_step_wind:g}°气象网格，按场站容量与能源类型估算",
            f"未来 {days_count()} 天按 15 分钟计算；第 5–7 天参考为主",
            f"已区分分期交直流容量，容配比假设 {settings.pv_dc_ac_ratio:g}、"
            f"系统损耗 {settings.pv_losses:.0%}（含逆变器）并按交流容量限幅；"
            "未知容量类型不计入预测",
            "采用默认设备参数，未计入限电、检修及故障影响",
            f"风电机型：陆上 {settings.wind_catalog_modern_from_year} 年起投运或年份未知"
            "按低风速机型，此前按通用功率曲线；海上按通用功率曲线并取海上格点",
            "统一北京时间；仅汇总平台运营目录，非全国实测电量",
            f"计算版本 {calculation_version(day)}",
            "功率曲线按区间起点对齐光伏与风电"
            if version_for_day(day) == "model-v4"
            else "旧版逐时标签口径",
        ],
    )


def phases_of(p) -> list[dict]:
    return (p.provenance or {}).get("phases") or []


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
        # 同一 GEM 分期只算一次。不能按「同名、同坐标、同容量」合并：GEM 里大量不同项目共用
        # 中文名与占位坐标，容量也可能相同（如白沟商业屋顶光伏 II–VI 期均为 1.6 MW）。
        phase_ids = tuple(sorted(str(ph["id"]) for ph in phases_of(p) if ph.get("id")))
        key = (p.type, phase_ids) if phase_ids else (p.type, p.source, p.source_id)
        if key in seen:
            duplicate += 1
            continue
        seen.add(key)
        rows.append(p)
    return rows, duplicate, invalid


def grid_step(station_type: str) -> float:
    """该类型取气象用多大的网格。光伏 1°、风电 0.25°，见 settings 的说明。"""
    return (
        settings.fleet_grid_step_wind if station_type == "wind" else settings.fleet_grid_step_solar
    )


def cell(p) -> tuple[str, float, float, str | None]:
    """分组键 = (类型, 格心纬度, 格心经度, 格点选择)。

    类型进键是必须的：两种类型步长不同，同一座标附近的光伏与风电属于不同的格。
    格心取整到 4 位小数，避免 0.25 这类步长把浮点噪声带进缓存键与请求参数。
    海上风电的格点选择为 sea，与同格心的陆上场站分开取气象；其余为 None（上游默认 land）。
    """
    step = grid_step(p.type)
    return (
        p.type,
        round(math.floor(p.latitude / step) * step + step / 2, 4),
        round(math.floor(p.longitude / step) * step + step / 2, 4),
        catalog_cell_selection(p),
    )


def coord_key(lat: float, lon: float, selection: str | None = None) -> str:
    """天气缓存键认坐标与格点选择，不认类型 —— 同一座标的光伏与陆上风电本就该共用一份气象。"""
    return f"{lat},{lon}" + (f",{selection}" if selection else "")


def calculate_cell(
    plants, raw, model, day, lat: float, lon: float, cache=None, weather_digest=None
):
    """一个网格内全部场站的未来 N 天单位容量曲线。返回 [(plant, [day0, day1, …])]，
    某天不可算时该位为 None；今日不可算的场站不计入覆盖。"""
    fc = weather.parse_forecast(raw, model=model, require_quarter=True)
    if fc.current_hour().date().isoformat() != day:
        raise ValueError("统计日期已变化")
    curves: dict[tuple, list[np.ndarray | None]] = {}
    results = []
    for p in plants:
        hub = wind.default_hub_height() if p.type == "wind" else None
        turbine = catalog_turbine_class(p)
        basis, blocked = catalog_basis(p)
        if blocked:
            continue
        dc_ratio, ac_ratio = (v / p.capacity_kw for v in basis)
        key = (p.type, hub, turbine, round(dc_ratio, 6), round(ac_ratio, 6))
        if key not in curves:
            cache_key = (
                cache.key(
                    weather_digest=weather_digest,
                    model=model,
                    day=day,
                    latitude=lat,
                    longitude=lon,
                    parameters=key,
                    days=days_count(),
                )
                if cache is not None and weather_digest is not None
                else None
            )
            cached = cache.get(cache_key, days_count()) if cache_key else None
            if cached is not None:
                curves[key] = cached
        if key not in curves:
            st = Station(
                type=p.type,
                latitude=lat,
                longitude=lon,
                capacity_kw=1,
                tilt=None,
                azimuth=None,
                hub_height=hub,
                turbine_class=turbine,
            )
            st._pv_capacity = (dc_ratio, ac_ratio)
            per_day: list[np.ndarray | None] = []
            for k in range(days_count()):
                # 聚合前不按 1 kW 曲线取三位小数，防止舍入误差被场站容量放大。
                prep = energy.prepare(st, fc, day_offset=k)
                curve = energy.hourly_power(st, prep, fc.tz).to_numpy(dtype=float)
                per_day.append(curve if np.isfinite(curve).all() else None)
            curves[key] = per_day
            if cache_key:
                try:
                    cache.put(cache_key, per_day)
                except OSError:
                    log.warning("网格曲线缓存写入失败，仍使用本次计算结果", exc_info=True)
        per_day = curves[key]
        if any(c is not None for c in per_day):
            results.append((p, [c * p.capacity_kw if c is not None else None for c in per_day]))
    return results


async def build(http, model: str, day: str, plants) -> None:
    path = directory() / f"{model}-{day}.json"
    out = blank(model, day)
    newest = max((p.updated_at for p in plants if p.updated_at is not None), default=None)
    out.catalog_revision = f"{len(plants)}:{newest.isoformat() if newest else ''}"
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
    total = np.zeros((n_days, 96))
    solar_kwh = np.zeros(n_days)
    wind_kwh = np.zeros(n_days)
    covered_days = np.zeros(n_days, dtype=int)
    covered_capacity = np.zeros(n_days)
    common_total = np.zeros(n_days)
    regions: list[dict[str, list]] = [{} for _ in range(n_days)]
    covered = set()
    coverage_by_plant = {}
    cache_path = directory() / f"{model}-{day}-weather-15m.json"
    saved = load(cache_path) or {}
    # 每个模型每个北京日只拉一次气象：同日新批次、元数据缺失、快照重算都复用当天已取得的
    # 坐标，起报以当天首轮为准；只有分辨率或预报天数变化才作废。docs/04 §二
    if (
        saved.get("resolution_minutes") != 15
        or saved.get("forecast_days", n_days + 1) != n_days + 1
    ):
        saved = {}
    if saved.get("basis"):
        out.basis = ForecastBasis.model_validate(saved["basis"])
        out.batch_stamp = saved.get("batch_stamp") or weather.batch_stamp(None)
    else:
        # 批量请求没有逐格元数据，当天首轮按模型取一次
        meta = await weather.get_model_meta(http, model)
        out.basis = weather.Forecast(tz=TZ, hourly=pd.DataFrame(), model=model, meta=meta).basis()
        out.batch_stamp = saved.get("batch_stamp") or weather.batch_stamp(meta)
    from app.services.weather_cells import WeatherCells, prune_dated_cache

    raw_cache = WeatherCells(
        directory().parent.parent / "fleet-weather-cells" / model / day, saved.get("cell_files")
    )
    curve_cache = CurveCache(directory().parent.parent / "fleet-curve-cache" / model / day)
    fetched = saved.get("fetched", {})
    saved_at = saved.get("saved_at", datetime.now(UTC).timestamp())
    # 缺失坐标可以续拉，但每天的拉取轮数有上限，持续失败的坐标不会整天反复计费。
    rounds = int(saved.get("fetch_rounds", 0))
    can_fetch = rounds < settings.fleet_fetch_rounds_per_day
    counted = False
    stop = False

    def save_index() -> None:
        write(
            cache_path,
            {
                "saved_at": saved_at,
                "batch_stamp": out.batch_stamp,
                "basis": out.basis.model_dump(),
                "resolution_minutes": 15,
                "forecast_days": n_days + 1,
                "cell_files": raw_cache.references,
                "fetched": fetched,
                "fetch_rounds": rounds,
            },
        )

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
            PowerPoint(time=f"{dates[k]}T{h // 4:02d}:{h % 4 * 15:02d}:00+08:00", value=float(v))
            for h, v in enumerate(total[k])
        ]

    def publish():
        if out.status == "building":
            return
        out.generated_at = datetime.now(UTC).isoformat()
        # 顶层字段始终是今日，与历史留档兼容；未来各天在 days 里。docs/17 §二
        out.energy_kwh = float(total[0].sum()) * 0.25 if out.covered_count else None
        out.solar_kwh = float(solar_kwh[0])
        out.wind_kwh = float(wind_kwh[0])
        out.power_kw = points(0)
        out.regions = region_list(0)
        if fetched:
            out.basis.fetched_at = min(fetched.values())
        out.days = [
            FleetDay(
                resolution_minutes=15,
                date=dates[k],
                weekday=date.fromisoformat(dates[k]).isoweekday(),
                lead_days=k,
                energy_kwh=float(total[k].sum()) * 0.25 if covered_days[k] else None,
                solar_kwh=float(solar_kwh[k]),
                wind_kwh=float(wind_kwh[k]),
                power_kw=points(k),
                regions=region_list(k),
                covered_count=int(covered_days[k]),
                covered_capacity_kw=float(covered_capacity[k]),
                failed_count=out.eligible_count - int(covered_days[k]),
                status=(
                    "building"
                    if out.status == "building"
                    else "ready"
                    if covered_days[k] == out.eligible_count and out.eligible_count
                    else "partial"
                    if covered_days[k]
                    else "error"
                ),
                common_energy_kwh=float(common_total[k]) if out.common_covered_count else None,
            )
            for k in range(n_days)
        ]
        # 中间批次只用于计算，不覆盖用户正在查看的已完成快照。
        if out.status != "building":
            previous = load(path)
            if out.energy_kwh is not None or not usable(previous, day):
                published = out.model_dump()
                if out.status == "error":
                    published["_retry_at"] = time.time() + 1800
                write(path, published)
            else:
                previous["_retry_at"] = time.time() + 1800
                previous["message"] = "本轮更新未取得有效数据，保留上次预测"
                write(path, previous)
        log.info(
            "fleet curve cache %s: hits=%d misses=%d covered=%d",
            model,
            curve_cache.hits,
            curve_cache.misses,
            out.covered_count,
        )

    publish()
    per_request = settings.fleet_coords_per_request
    pacer = Pacer(settings.fleet_coords_per_minute)
    for start in range(0, len(keys), per_request):
        if day != day_key():
            out.message = "统计日期已变化，请刷新"
            break
        batch = keys[start : start + per_request]
        # 同一座标可能同时是光伏格与风电格，只取一次。cell_selection 对整个请求生效，
        # 海上格点单独成一次请求。
        missing: dict[str | None, list[tuple[float, float]]] = defaultdict(list)
        pending: set[str] = set()
        for _type, lat, lon, selection in batch:
            ck = coord_key(lat, lon, selection)
            if ck not in raw_cache and ck not in pending:
                pending.add(ck)
                missing[selection].append((lat, lon))
        if missing and not can_fetch:
            out.message = "今日气象拉取轮数已用完，显示已覆盖范围"
        elif missing:
            if not counted:
                rounds += 1
                counted = True
                save_index()
            for selection, coords in missing.items():
                await pacer.take(len(coords))  # 只为真正出网的坐标付时间
                params = {
                    "latitude": ",".join(str(a) for a, _ in coords),
                    "longitude": ",".join(str(b) for _, b in coords),
                    "models": model,
                    "minutely_15": ",".join(FIELDS),
                    "timezone": TZ,
                    "forecast_days": n_days + 1,
                    "wind_speed_unit": "ms",
                }
                if selection:
                    params["cell_selection"] = selection
                try:
                    await shared.take(len(coords))
                    r = await weather_get(
                        http, f"{settings.open_meteo_base}/forecast", params=params, timeout=40
                    )
                    if r.status_code == 429:
                        shared.retry_after(r.headers.get("Retry-After"))
                        out.message = "气象服务限流，已保存当前覆盖结果，稍后继续"
                        stop = True
                        break
                    r.raise_for_status()
                    payload = r.json()
                    payload = payload if isinstance(payload, list) else [payload]
                    if len(payload) != len(coords):
                        raise ValueError("批量响应数量不一致")
                    for (lat, lon), raw in zip(coords, payload, strict=True):
                        ck = coord_key(lat, lon, selection)
                        raw_cache[ck] = raw
                        fetched[ck] = datetime.now(ZoneInfo(TZ)).isoformat()
                    save_index()
                except Exception:
                    log.warning("fleet weather batch failed: %s %s", model, start, exc_info=True)
                    out.message = "部分区域气象数据暂不可用，显示已覆盖范围"
        for key in batch:
            _type, lat, lon, selection = key
            ck = coord_key(lat, lon, selection)
            raw = raw_cache.get(ck)
            if not raw:
                continue
            try:
                results = await asyncio.to_thread(
                    calculate_cell,
                    groups[key],
                    raw,
                    model,
                    day,
                    lat,
                    lon,
                    curve_cache,
                    raw_cache.references[ck],
                )
            except Exception:
                log.warning("fleet cell failed: %s %s", model, key, exc_info=True)
                continue
            for p, per_day in results:
                if p.id in covered:
                    continue
                covered.add(p.id)
                coverage_by_plant[p.id] = [dates[k] for k, c in enumerate(per_day) if c is not None]
                if per_day[0] is not None:
                    out.covered_count += 1
                    out.covered_capacity_kw += p.capacity_kw
                if all(c is not None for c in per_day):
                    out.common_covered_count += 1
                    out.common_capacity_kw += p.capacity_kw
                    common_total += np.array([c.sum() * 0.25 for c in per_day])
                for k, curve in enumerate(per_day):
                    if curve is None:
                        continue
                    total[k] += curve
                    covered_days[k] += 1
                    covered_capacity[k] += p.capacity_kw
                    energy = float(curve.sum()) * 0.25
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
    out.failed_count = out.eligible_count - out.covered_count
    # 「算完了」与「覆盖了整个目录」是两件事。重复、字段非法、容量口径未核验的场站
    # 被主动排除，永远不会进入 covered，拿 total_count 判断会让状态永远停在 partial。
    # 覆盖程度由 covered_count / total_count 与 covered_capacity_kw / total_capacity_kw 表达。
    out.status = (
        "ready"
        if out.eligible_count and bool((covered_days == out.eligible_count).all())
        else "partial"
        if covered_days.any()
        else "error"
    )
    publish()
    if out.status in ("ready", "partial"):
        from app.services.prediction_archive import save_fleet_inputs

        out.input_archive_id = await asyncio.to_thread(
            save_fleet_inputs, out, verified, raw_cache, fetched, coverage_by_plant
        )
        publish()
    from app.services import fleet_history

    fleet_history.capture(out.model_dump(), calculation_version(day))
    fleet_history.capture_leads(out.model_dump())
    await asyncio.to_thread(raw_cache.prune_before, date.fromisoformat(day) - timedelta(days=2))
    await asyncio.to_thread(
        prune_dated_cache,
        curve_cache.folder,
        date.fromisoformat(day) - timedelta(days=2),
    )
    # 留两天快照，清理旧天气文件，避免磁盘长期增长。
    for old in directory().glob("*.json"):
        if old.stat().st_mtime < datetime.now(UTC).timestamp() - 172800:
            old.unlink(missing_ok=True)


def usable(saved: dict | None, day: str) -> bool:
    return bool(
        saved
        and saved.get("calculation_version") == calculation_version(day)
        and saved.get("status") in ("ready", "partial")
        and saved.get("energy_kwh") is not None
    )


def carry_previous(model: str, day: str) -> dict | None:
    """跨日按实际日期续用昨日预报，不把昨日电量冒充今日，也不补造第七天。"""
    yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    previous = load(directory() / f"{model}-{yesterday}.json")
    if not usable(previous, day):
        return None
    by_date = {d["date"]: d for d in previous.get("days", [])}
    if by_date.get(day, {}).get("energy_kwh") is None:
        return None
    result = FleetPrediction.model_validate(previous)
    result.date = day
    result.days = []
    for k in range(days_count()):
        target = date.fromisoformat(day) + timedelta(days=k)
        row = by_date.get(target.isoformat())
        if row:
            row = {**row, "lead_days": k}
        else:
            row = dict(
                date=target.isoformat(),
                weekday=target.isoweekday(),
                lead_days=k,
                energy_kwh=None,
                solar_kwh=0,
                wind_kwh=0,
                power_kw=[],
                resolution_minutes=15,
                status="queued",
            )
        result.days.append(FleetDay.model_validate(row))
    today = result.days[0]
    for field in (
        "energy_kwh",
        "solar_kwh",
        "wind_kwh",
        "power_kw",
        "covered_count",
        "covered_capacity_kw",
        "failed_count",
        "regions",
        "resolution_minutes",
    ):
        setattr(result, field, getattr(today, field))
    result.status = "partial"
    result.message = "新一轮预报准备中，当前使用上一批对应日期的预测"
    return {**result.model_dump(), "_carried": True}


async def catalog_revision() -> str:
    async with SessionLocal() as db:
        count, updated = (
            await db.execute(
                select(func.count(), func.max(CatalogPlant.updated_at))
                .select_from(CatalogPlant)
                .where(CatalogPlant.status == "operating")
            )
        ).one()
    return f"{count}:{updated.isoformat() if updated else ''}"


async def operating_plants() -> list[CatalogPlant]:
    async with SessionLocal() as db:
        rows = await db.execute(select(CatalogPlant).where(CatalogPlant.status == "operating"))
        return list(rows.scalars().all())


async def ensure(http, model: str) -> FleetPrediction:
    if model not in MODELS:
        raise ValueError("不支持的模型")
    day = day_key()
    key = f"{model}-{day}"
    path = directory() / f"{key}.json"
    saved = load(path)
    if not usable(saved, day):
        carried = carry_previous(model, day)
        if carried:
            saved = carried
            write(path, saved)
    initial = (
        FleetPrediction.model_validate(saved)
        if usable(saved, day)
        or (
            saved
            and saved.get("status") == "error"
            and saved.get("calculation_version") == calculation_version(day)
        )
        else blank(model, day)
    )
    if saved and saved.get("_carried") and beijing_now().hour < settings.fleet_refresh_hour:
        # Open-Meteo 日额度在 UTC 零点（北京 08:00）重置；此前沿用上一日快照里对应日期的
        # 预测，不开当天这一轮整目录拉取。docs/04 §二
        return initial
    if saved and saved.get("_retry_at", 0) > time.time():
        return initial
    task = _jobs.get(key)
    if task and not task.done():
        return initial.model_copy(update={"updating": True})
    if time.monotonic() - _checked.get(key, -float("inf")) < 60:
        return initial
    if not usable(saved, day):
        write(path, initial.model_dump())

    async def run():
        async with _gate:
            try:
                # 数据库检查也在后台进行，读快照的请求无需等待连接池。
                revision = await catalog_revision()
                if saved and not saved.get("_carried"):
                    age = time.time() - datetime.fromisoformat(saved["generated_at"]).timestamp()
                    # 当天已完成就不再重算，新批次也不触发；部分覆盖每半小时续算，
                    # 气象只补拉缺失坐标。
                    if (
                        usable(saved, day)
                        and saved.get("catalog_revision") == revision
                        and (saved["status"] == "ready" or age < 1800)
                    ):
                        return
                await build(http, model, day, await operating_plants())
            except Exception:
                log.exception("fleet prediction failed: %s", key)
                failed = load(path) or initial.model_dump()
                if usable(failed, day):
                    failed["message"] = "更新失败，保留上次预测"
                else:
                    failed.update(status="error", message="汇总暂不可用，稍后自动重试")
                failed["_retry_at"] = time.time() + 1800
                write(path, failed)
            finally:
                _checked[key] = time.monotonic()

    _jobs[key] = asyncio.create_task(run())
    for old in list(_jobs):
        if old != key and _jobs[old].done():
            del _jobs[old]
            _checked.pop(old, None)
    return initial.model_copy(update={"updating": True})


async def shutdown():
    for task in _jobs.values():
        task.cancel()
    await asyncio.gather(*_jobs.values(), return_exceptions=True)
    _jobs.clear()
    _checked.clear()
