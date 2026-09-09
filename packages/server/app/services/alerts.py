"""预警：规则、扫描、查询。docs/07 §五、docs/06 §九

两类来源写同一张表：`forecast` 来自气象预报规则，`satellite` 来自
Himawari 两帧光流的云团外推（services/satellite）。
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import utcnow
from app.metrics import solar, wind
from app.models import Alert, Station
from app.schemas.common import AlertLevel
from app.schemas.home import AlertSummary
from app.services import satellite
from app.services.weather import Forecast

# ── 规则参数（docs/07 §七 预警）──
DEDUP_WINDOW = timedelta(hours=2)
CLEAR_CHECK_MAX_GAP = timedelta(minutes=20)  # 15 分钟调度加 5 分钟余量，超时重计稳定期。
CLEAR_STABLE_WINDOW = timedelta(minutes=30)  # 条件消失并持续这么久才解除，避免临界抖动。§5.3
SATELLITE_ALERT_TTL = timedelta(hours=2)  # 外推时效上限，卫星断供时预警最多保留这么久
LOOKAHEAD_HOURS = 6  # 云层下降只看未来 6 小时
MIN_DROP = 10.0  # 降幅低于此不触发
MODERATE_DROP = settings.alert_drop_moderate
SEVERE_DROP = settings.alert_drop_severe
MIN_KT_NOW = 0.35  # 当前晴空指数太低（本来就阴）不谈「下降」
MIN_CLEAR_GHI = 100.0  # 晴空小时均值低于此（日出日落边缘）不参与比较：kt 噪声大且对发电无关紧要
WIND_MODERATE, WIND_SEVERE = 15.0, 25.0
HEAT, COLD = 38.0, -10.0
RAIN_CODES = {65, 67, 82, 95, 96, 99}


@dataclass(frozen=True)
class Detected:
    kind: str
    level: str
    title: str
    description: str
    source: str = "forecast"


# ────────────────────────────── 规则 ──────────────────────────────


def detect_cloud_drop(fc: Forecast, latitude: float, longitude: float) -> Detected | None:
    """未来 6 小时内晴空指数相对当前的最大降幅。docs/07 §4.3、§5.1"""
    now_ts = fc.current_hour()
    end = now_ts + pd.Timedelta(hours=LOOKAHEAD_HOURS)
    window = fc.hourly.loc[now_ts:end]
    if len(window) < 2:
        return None

    # 预报辐射是前一小时均值，晴空分母取同口径的小时均值；用整点瞬时值会让早晨的 kt 系统性偏低
    cs = solar.clearsky_hourly_mean(latitude, longitude, fc.tz, pd.DatetimeIndex(window.index))
    ghi = window["shortwave_radiation"].astype(float)
    clear = cs["ghi"].astype(float)

    # 只在白天且晴空辐射有意义的时段比较
    valid = clear >= MIN_CLEAR_GHI
    if valid.sum() < 2:
        return None
    kt = (ghi / clear).where(valid)

    kt_now = float(kt.iloc[0]) if valid.iloc[0] else None
    if kt_now is None or kt_now < MIN_KT_NOW:
        return None

    future = kt.iloc[1:].dropna()
    if future.empty:
        return None
    worst_ts = future.idxmin()
    drop = (kt_now - float(future.min())) / kt_now * 100
    if drop < MIN_DROP:
        return None

    level = "severe" if drop >= SEVERE_DROP else "moderate" if drop >= MODERATE_DROP else "minor"
    at = pd.Timestamp(worst_ts).strftime("%H:%M")
    drop_i = int(round(drop / 5) * 5)  # 取整到 5，避免「下降 23%」这种假精度
    return Detected(
        kind="cloud",
        level=level,
        title=f"{at}后云量增加，预计辐射下降{drop_i}%",
        description=(
            f"预报显示 {at} 前后云量将逐步增加，辐射较当前下降约 {drop_i}%，"
            "可能对光伏发电产生影响，建议提前调整发电计划。"
        ),
    )


def _alert_wind_speed(window: pd.DataFrame, station: Station | None) -> tuple[pd.Series, str]:
    """强风规则用的风速。风电站取轮毂高度（与功率曲线同口径，切出判断才对得上），
    其余取 10 m。返回 (风速, 高度文案)。"""
    if station is not None and station.type == "wind":
        levels = {
            h: window[col].astype(float) for h, col in wind.LEVEL_COLUMNS.items() if col in window
        }
        if levels:
            hub = station.hub_height
            if hub is None:
                hub = wind.default_hub_height()
            v_hub, _ = wind.hub_wind_speed(levels, hub)
            return v_hub, f"轮毂高度 {hub:.0f} m"
    return window["wind_speed_10m"].astype(float), "10 m"


def detect_weather(fc: Forecast, station: Station | None = None) -> list[Detected]:
    """天气异常：未来 24 小时。docs/07 §5.2"""
    now_ts = fc.current_hour()
    window = fc.hourly.loc[now_ts : now_ts + pd.Timedelta(hours=24)]
    out: list[Detected] = []

    ws, height = _alert_wind_speed(window, station)
    if ws.notna().any() and ws.max() >= WIND_SEVERE:
        at = pd.Timestamp(ws.idxmax()).strftime("%H:%M")
        out.append(
            Detected(
                "wind",
                "severe",
                f"{at}前后风速超过切出风速",
                f"预报{height}最大风速 {ws.max():.0f} m/s，风机将停机保护，请关注设备状态。",
            )
        )
    elif ws.notna().any() and ws.max() >= WIND_MODERATE:
        at = pd.Timestamp(ws.idxmax()).strftime("%H:%M")
        out.append(
            Detected(
                "wind",
                "moderate",
                f"{at}前后有强风",
                f"预报{height}最大风速 {ws.max():.0f} m/s，注意光伏组件与风机安全。",
            )
        )

    codes = window["weather_code"].astype(float)
    rain = codes[codes.isin(RAIN_CODES)]
    if not rain.empty:
        at = pd.Timestamp(rain.index[0]).strftime("%H:%M")
        out.append(
            Detected(
                "rain",
                "moderate",
                f"{at}前后有暴雨或雷暴",
                "预报有强降水或雷暴，辐射将显著下降，注意防雷与排水。",
            )
        )

    t = window["temperature_2m"].astype(float)
    if t.max() >= HEAT:
        out.append(
            Detected(
                "heat",
                "moderate",
                f"高温预警，最高 {t.max():.0f}℃",
                "高温使光伏组件效率下降，并加大设备散热压力。",
            )
        )
    if t.min() <= COLD:
        out.append(
            Detected(
                "cold",
                "moderate",
                f"低温预警，最低 {t.min():.0f}℃",
                "低温可能影响设备运行，注意防冻。",
            )
        )
    return out


def detect_cloud_motion(scene: satellite.CloudScene, station: Station, tz: str) -> Detected | None:
    """卫星短临：来向锥内云团 2 小时内到站。docs/07 §4.1、§4.2"""
    est = satellite.estimate_motion(scene, station)
    if est is None or est.impact_minutes is None or est.distance_km is None:
        return None
    if est.covered:
        return Detected(
            kind="cloud_motion",
            level="moderate",
            title="观测显示云团过境，需关注辐射变化",
            description=(
                f"卫星云图显示站点上空有云，云团向{est.heading_text}方向移动，"
                f"速度约 {est.speed_kmh:.0f} km/h。"
            ),
            source="satellite",
        )
    m = est.impact_minutes
    level = "severe" if m <= 30 else "moderate" if m <= 60 else "minor"
    at = satellite.local_time(scene.observed_at, m, tz)
    return Detected(
        kind="cloud_motion",
        level=level,
        title=f"云团逼近，观测推算约 {at} 前后影响",
        description=(
            f"卫星云图显示{est.origin_text}方向约 {est.distance_km:.0f} km 处有云团，"
            f"以约 {est.speed_kmh:.0f} km/h 向{est.heading_text}移动，"
            f"推算在该观测时刻后约 {m} 分钟到达，需结合最新影像复核。"
        ),
        source="satellite",
    )


def detect_all(
    fc: Forecast, station: Station, scene: satellite.CloudScene | None = None
) -> list[Detected]:
    found: list[Detected] = []
    if station.type == "solar":
        c = detect_cloud_drop(fc, station.latitude, station.longitude)
        if c:
            found.append(c)
        if scene is not None:
            m = detect_cloud_motion(scene, station, fc.tz)
            if m:
                found.append(m)
    found.extend(detect_weather(fc, station))
    return found


# ────────────────────────────── 扫描与持久化 ──────────────────────────────


async def _active_by_kind(db: AsyncSession, station_id: str) -> dict[str, Alert]:
    rows = (
        await db.execute(
            select(Alert).where(Alert.station_id == station_id, Alert.active.is_(True))
        )
    ).scalars()
    return {a.kind: a for a in rows}


async def apply_detections(
    db: AsyncSession, station: Station, found: list[Detected], *, satellite_known: bool = True
) -> None:
    """把检测结果落库：新预警 / 更新已有 / 解除消失的。docs/07 §5.3、§5.4

    satellite_known=False 表示这次没拿到卫星结论（上游故障），
    卫星类预警「未知」不等于「消失」，保留到自然过期（SATELLITE_ALERT_TTL）。
    """
    active = await _active_by_kind(db, station.id)
    now = utcnow()
    seen: set[str] = set()

    for d in found:
        seen.add(d.kind)
        cur = active.get(d.kind)
        if cur and now - cur.published_at < DEDUP_WINDOW:
            # 2 小时内同类型：更新内容不新建
            cur.level, cur.title, cur.description = d.level, d.title, d.description
            cur.last_detected_at = now
            cur.clear_since = None
            cur.last_clear_check_at = None
            continue
        if cur:
            cur.active = False
        db.add(
            Alert(
                station_id=station.id,
                kind=d.kind,
                level=d.level,
                source=d.source,
                title=d.title,
                description=d.description,
                published_at=now,
                last_detected_at=now,
            )
        )

    # 之前在生效、这次没检测到的 → 条件消失并持续 30 分钟稳定后解除
    for kind, cur in active.items():
        if kind in seen or cur.level == "cleared":
            continue
        satellite_expired = (
            cur.source == "satellite"
            and not satellite_known
            and now - cur.published_at >= SATELLITE_ALERT_TTL
        )
        if cur.source == "satellite" and not satellite_known and not satellite_expired:
            cur.clear_since = None
            cur.last_clear_check_at = None
            continue
        if not satellite_expired:
            if (
                cur.clear_since is None
                or cur.last_clear_check_at is None
                or now - cur.last_clear_check_at > CLEAR_CHECK_MAX_GAP
            ):
                cur.clear_since = now
            cur.last_clear_check_at = now
            if now - cur.clear_since < CLEAR_STABLE_WINDOW:
                continue
        cur.active = False
        db.add(
            Alert(
                station_id=station.id,
                kind=kind,
                level="cleared",
                source=cur.source,
                title=_cleared_title(kind),
                description="影响因素已消退，发电条件逐步恢复。",
                published_at=now,
                active=False,
            )
        )


def _cleared_title(kind: str) -> str:
    return {
        "cloud": "云系逐渐远离，辐射将恢复",
        "cloud_motion": "云团已过境，辐射逐步恢复",
        "wind": "强风减弱，风险解除",
        "rain": "降水结束，风险解除",
        "heat": "高温缓解，风险解除",
        "cold": "低温缓解，风险解除",
    }.get(kind, "风险解除")


# 读请求顺手扫描与定时扫描可能同时跑同一站点，各自看不到对方未提交的插入 → 重复预警。
# 进程内按站点串行；多实例部署时换成数据库行锁。
_scan_locks: dict[str, asyncio.Lock] = {}


async def scan_station(
    db: AsyncSession, station: Station, fc: Forecast, sat: satellite.SceneResult | None = None
) -> int:
    lock = _scan_locks.setdefault(station.id, asyncio.Lock())
    async with lock:
        scene = sat.scene if sat else None
        found = detect_all(fc, station, scene)
        await apply_detections(db, station, found, satellite_known=sat.known if sat else False)
        await db.commit()
        return len(found)


# ────────────────────────────── 查询 ──────────────────────────────

_LEVEL_RANK = {"severe": 3, "moderate": 2, "minor": 1, "cleared": 0}


def to_summary(a: Alert, tz: str) -> AlertSummary:
    published = a.published_at.replace(tzinfo=UTC).astimezone(ZoneInfo(tz))
    return AlertSummary(
        id=a.id,
        level=AlertLevel(a.level),
        title=a.title,
        description=a.description,
        published_at=published.isoformat(timespec="minutes"),
        station_id=a.station_id,
        source=a.source,
    )


async def current_alert(db: AsyncSession, station_id: str, tz: str) -> AlertSummary | None:
    """当前生效中等级最高的一条。"""
    rows = (
        (
            await db.execute(
                select(Alert).where(Alert.station_id == station_id, Alert.active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    best = max(rows, key=lambda a: (_LEVEL_RANK.get(a.level, 0), a.published_at))
    return to_summary(best, tz)


async def list_alerts(
    db: AsyncSession,
    station_id: str,
    tz: str,
    level: str | None,
    limit: int,
    before: datetime | None,
) -> tuple[list[AlertSummary], datetime | None]:
    q = select(Alert).where(Alert.station_id == station_id)
    if level and level != "all":
        q = q.where(Alert.level == level)  # cleared 只在 all 里出现
    if before:
        q = q.where(Alert.published_at < before)
    q = q.order_by(Alert.published_at.desc()).limit(limit + 1)
    rows = (await db.execute(q)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    cursor = rows[-1].published_at if has_more and rows else None
    return [to_summary(a, tz) for a in rows], cursor


async def deactivate_all(db: AsyncSession, station_id: str) -> None:
    """删站时用"""
    await db.execute(update(Alert).where(Alert.station_id == station_id).values(active=False))
