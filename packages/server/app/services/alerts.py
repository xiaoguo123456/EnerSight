"""预警：规则、扫描、查询。docs/07 §五、docs/06 §九

V1 全部基于气象预报。卫星短临（云团外推）依赖 Himawari 处理，接入后
作为另一个 source 写入同一张表。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import utcnow
from app.metrics import solar
from app.models import Alert, Station
from app.schemas.common import AlertLevel
from app.schemas.home import AlertSummary
from app.services.weather import Forecast

# ── 规则参数（docs/07 §七 预警）──
DEDUP_WINDOW = timedelta(hours=2)
LOOKAHEAD_HOURS = 6  # 云层下降只看未来 6 小时
MIN_DROP = 10.0  # 降幅低于此不触发
MODERATE_DROP = settings.alert_drop_moderate
SEVERE_DROP = settings.alert_drop_severe
MIN_KT_NOW = 0.35  # 当前晴空指数太低（本来就阴）不谈「下降」
WIND_MODERATE, WIND_SEVERE = 15.0, 25.0
HEAT, COLD = 38.0, -10.0
RAIN_CODES = {65, 67, 82, 95, 96, 99}


@dataclass(frozen=True)
class Detected:
    kind: str
    level: str
    title: str
    description: str


# ────────────────────────────── 规则 ──────────────────────────────


def detect_cloud_drop(fc: Forecast, latitude: float, longitude: float) -> Detected | None:
    """未来 6 小时内晴空指数相对当前的最大降幅。docs/07 §4.3、§5.1"""
    now_ts = fc.current_hour()
    end = now_ts + pd.Timedelta(hours=LOOKAHEAD_HOURS)
    window = fc.hourly.loc[now_ts:end]
    if len(window) < 2:
        return None

    cs = solar.clearsky(latitude, longitude, fc.tz, pd.DatetimeIndex(window.index))
    ghi = window["shortwave_radiation"].astype(float)
    clear = cs["ghi"].astype(float)

    # 只在白天且晴空辐射有意义的时段比较
    valid = clear > 50
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


def detect_weather(fc: Forecast) -> list[Detected]:
    """天气异常：未来 24 小时。docs/07 §5.2"""
    now_ts = fc.current_hour()
    window = fc.hourly.loc[now_ts : now_ts + pd.Timedelta(hours=24)]
    out: list[Detected] = []

    ws = window["wind_speed_10m"].astype(float)
    if ws.max() >= WIND_SEVERE:
        at = pd.Timestamp(ws.idxmax()).strftime("%H:%M")
        out.append(
            Detected(
                "wind",
                "severe",
                f"{at}前后风速超过切出风速",
                f"预报最大风速 {ws.max():.0f} m/s，风机将停机保护，请关注设备状态。",
            )
        )
    elif ws.max() >= WIND_MODERATE:
        at = pd.Timestamp(ws.idxmax()).strftime("%H:%M")
        out.append(
            Detected(
                "wind",
                "moderate",
                f"{at}前后有强风",
                f"预报最大风速 {ws.max():.0f} m/s，注意光伏组件与风机安全。",
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


def detect_all(fc: Forecast, station: Station) -> list[Detected]:
    found: list[Detected] = []
    if station.type == "solar":
        c = detect_cloud_drop(fc, station.latitude, station.longitude)
        if c:
            found.append(c)
    found.extend(detect_weather(fc))
    return found


# ────────────────────────────── 扫描与持久化 ──────────────────────────────


async def _active_by_kind(db: AsyncSession, station_id: str) -> dict[str, Alert]:
    rows = (
        await db.execute(
            select(Alert).where(Alert.station_id == station_id, Alert.active.is_(True))
        )
    ).scalars()
    return {a.kind: a for a in rows}


async def apply_detections(db: AsyncSession, station: Station, found: list[Detected]) -> None:
    """把检测结果落库：新预警 / 更新已有 / 解除消失的。docs/07 §5.3、§5.4"""
    active = await _active_by_kind(db, station.id)
    now = utcnow()
    seen: set[str] = set()

    for d in found:
        seen.add(d.kind)
        cur = active.get(d.kind)
        if cur and now - cur.published_at < DEDUP_WINDOW:
            # 2 小时内同类型：更新内容不新建
            cur.level, cur.title, cur.description = d.level, d.title, d.description
            continue
        if cur:
            cur.active = False
        db.add(
            Alert(
                station_id=station.id,
                kind=d.kind,
                level=d.level,
                source="forecast",
                title=d.title,
                description=d.description,
                published_at=now,
            )
        )

    # 之前在生效、这次没检测到的 → 解除
    for kind, cur in active.items():
        if kind in seen or cur.level == "cleared":
            continue
        cur.active = False
        db.add(
            Alert(
                station_id=station.id,
                kind=kind,
                level="cleared",
                source="forecast",
                title=_cleared_title(kind),
                description="影响因素已消退，发电条件逐步恢复。",
                published_at=now,
                active=False,
            )
        )


def _cleared_title(kind: str) -> str:
    return {
        "cloud": "云系逐渐远离，辐射将恢复",
        "wind": "强风减弱，风险解除",
        "rain": "降水结束，风险解除",
        "heat": "高温缓解，风险解除",
        "cold": "低温缓解，风险解除",
    }.get(kind, "风险解除")


async def scan_station(db: AsyncSession, station: Station, fc: Forecast) -> int:
    found = detect_all(fc, station)
    await apply_detections(db, station, found)
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
