"""报告：生成、存档、读取。docs/08 §三

当日报告按需生成或更新十分钟缓存，历史报告只读已有存档。
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import generate as ai
from app.ai.input import PERIODS, build_input
from app.ai.schema import AIReport
from app.config import settings
from app.db import upsert_insert
from app.errors import ApiError, DataUnavailable
from app.models import DailyGeneration, Report, Station
from app.schemas.common import Coord, MetricWithDelta
from app.schemas.report import AIReportResponse, ReportPeriodOut, ReportSummary
from app.services import alerts
from app.services.home import build_station_view


def _delta(now: float | None, yesterday: float | None) -> float | None:
    if now is None or yesterday is None or yesterday == 0:
        return None
    return round((now - yesterday) / abs(yesterday) * 100, 1)


async def _summary(db: AsyncSession, station: Station, today_kwh: float, day: date) -> dict:
    """数据摘要四项，均带昨日环比。算出来的，不经过模型。docs/08 §4.1"""
    # 严格取昨日。拿「最近一条更早记录」冒充昨日会把「较昨日」说成较三天前；
    # 昨日缺记录时按 docs/07 §3.3 隐藏环比。
    y = (
        await db.execute(
            select(DailyGeneration.kwh).where(
                DailyGeneration.station_id == station.id,
                DailyGeneration.day == day - timedelta(days=1),
            )
        )
    ).scalar()
    cap = station.capacity_kw or 1.0
    f = settings.co2_factor_kg_per_kwh
    p = settings.tariff_yuan_per_kwh
    return {
        "generation": {"value": round(today_kwh, 1), "delta_percent": _delta(today_kwh, y)},
        "equivalent_hours": {
            "value": round(today_kwh / cap, 2),
            "delta_percent": _delta(today_kwh / cap, y / cap if y else None),
        },
        "co2_reduction": {
            "value": round(today_kwh * f, 1),
            "delta_percent": _delta(today_kwh * f, y * f if y else None),
        },
        "estimated_revenue": {
            "value": round(today_kwh * p, 0),
            "delta_percent": _delta(today_kwh * p, y * p if y else None),
        },
    }


async def generate_and_store(
    db: AsyncSession, http: httpx.AsyncClient, station: Station, day: date | None = None
) -> Report:
    v = await build_station_view(http, station, Coord.WGS84, db)
    day = day or v.forecast.now().date()
    if v.snapshot.index is None or v.snapshot.daily_kwh is None:
        # 气象缺测时指数不可算，不能拿 0 分生成报告
        raise DataUnavailable("气象数据暂不完整，报告稍后生成")
    alert = await alerts.current_alert(db, station.id, v.forecast.tz)
    note = (
        f"{v.snapshot.blocked}，发电量按目录申报容量估算，仅供参考" if v.snapshot.blocked else None
    )
    inp = build_input(station, v.forecast, v.snapshot.index, v.snapshot.daily_kwh, alert, note)

    gen = await ai.generate(inp)
    content = gen.report.model_dump()
    content["_meta"] = {
        "version": 2,
        "data_as_of": v.current.observed_at if v.current else None,
        "tariff_yuan_per_kwh": settings.tariff_yuan_per_kwh,
        "co2_factor_kg_per_kwh": settings.co2_factor_kg_per_kwh,
    }
    summary = await _summary(db, station, v.snapshot.daily_kwh, day)

    stmt = upsert_insert(db, Report).values(
        station_id=station.id,
        day=day,
        content=content,
        summary=summary,
        provider=gen.provider,
        is_fallback=gen.is_fallback,
        prompt_input=inp.render()[:4000],
        generated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["station_id", "day"],
        set_={
            "content": stmt.excluded.content,
            "summary": stmt.excluded.summary,
            "provider": stmt.excluded.provider,
            "is_fallback": stmt.excluded.is_fallback,
            "prompt_input": stmt.excluded.prompt_input,
            "generated_at": stmt.excluded.generated_at,
        },
    )
    await db.execute(stmt)
    await db.commit()
    return (
        await db.execute(
            select(Report)
            .where(Report.station_id == station.id, Report.day == day)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def get_or_generate(
    db: AsyncSession, http: httpx.AsyncClient, station: Station, day: date | None
) -> Report:
    v_tz = (await build_station_view(http, station, Coord.WGS84, db)).forecast.tz
    today = datetime.now(ZoneInfo(v_tz)).date()
    day = day or today
    row = (
        await db.execute(select(Report).where(Report.station_id == station.id, Report.day == day))
    ).scalar_one_or_none()
    if row is not None:
        # 公开目录不依赖个人站点定时任务；当天报告按需更新，历史保持原样。
        fresh = datetime.now(UTC).replace(tzinfo=None) - row.generated_at < timedelta(minutes=10)
        if day != today or (fresh and row.content.get("_meta", {}).get("version") == 2):
            return row
    if day != today:
        raise ApiError("REPORT_NOT_FOUND", "该日期暂无已存档报告", 404)
    return await generate_and_store(db, http, station, day)


def to_response(row: Report, station_summary, tz: str) -> AIReportResponse:
    report = AIReport.model_validate(row.content)
    ranges = {k: f"{h0:02d}:00 – {h1:02d}:00" for k, _, h0, h1 in PERIODS}
    generated = row.generated_at.replace(tzinfo=UTC).astimezone(ZoneInfo(tz))
    return AIReportResponse(
        station=station_summary,
        report_date=row.day.strftime("%Y年%-m月%-d日"),
        generated_at=generated.strftime("%Y年%-m月%-d日 %H:%M"),
        generated_at_iso=generated.isoformat(),
        data_as_of=row.content.get("_meta", {}).get("data_as_of"),
        tariff_yuan_per_kwh=row.content.get("_meta", {}).get("tariff_yuan_per_kwh"),
        co2_factor_kg_per_kwh=row.content.get("_meta", {}).get("co2_factor_kg_per_kwh"),
        is_fallback=row.is_fallback,
        method="rule" if row.provider == "rule" else "ai",
        verdict_title=report.verdict_title,
        verdict_detail=report.verdict_detail,
        periods=[
            ReportPeriodOut(**p.model_dump(), time_range=ranges[p.period]) for p in report.periods
        ],
        risk_title=report.risk_title,
        risk_detail=report.risk_detail,
        suggestions=report.suggestions,
        summary=ReportSummary(**{k: MetricWithDelta(**v) for k, v in row.summary.items()}),
    )
