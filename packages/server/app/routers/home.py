"""首页聚合与趋势。docs/06 §六、§八

游客可看公开电站；自建场站由 get_station 要求登录。docs/09 §4.3
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import OptionalUserDep, owner_of
from app.db import get_session
from app.schemas.common import Coord
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.schemas.home import (
    HomeResponse,
    MapOverviewResponse,
    StationDetailResponse,
    TrendMetric,
    TrendRange,
    TrendSeries,
)
from app.schemas.prediction import FleetPrediction, StationOutlook
from app.services import home as svc
from app.services import weather
from app.services.station import get_station

router = APIRouter(prefix="/v1", tags=["home"])
DbDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/home", response_model=Envelope[HomeResponse])
async def get_home(
    request: Request,
    user: OptionalUserDep,
    db: DbDep,
    coord: CoordQuery = Coord.WGS84,
    station_id: Annotated[str | None, Query()] = None,
) -> Envelope[HomeResponse]:
    data = await svc.build_home(db, request.app.state.http, owner_of(user), coord, station_id)
    return envelope(data, coord)


@router.get("/trends", response_model=Envelope[TrendSeries])
async def get_trends(
    request: Request,
    user: OptionalUserDep,
    db: DbDep,
    station_id: Annotated[str, Query()],
    metric: Annotated[TrendMetric, Query()] = TrendMetric.RADIATION,
    range_: Annotated[TrendRange, Query(alias="range")] = TrendRange.H24,
    day_offset: Annotated[int, Query(ge=0, le=6, description="24h 取今日起第几天")] = 0,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[TrendSeries]:
    station = await get_station(db, owner_of(user), station_id)
    # 只读事务在出网前结束，避免慢请求占满连接池。
    await db.commit()
    fc = await weather.station_forecast(request.app.state.http, station)
    hub = None
    if station.type == "wind":
        from app.metrics import wind

        hub = station.hub_height if station.hub_height is not None else wind.default_hub_height()
    return envelope(svc.build_trend(fc, metric, range_, day_offset, hub), coord)


@router.get("/stations/{station_id}/detail", response_model=Envelope[StationDetailResponse])
async def get_station_detail(
    request: Request,
    user: OptionalUserDep,
    db: DbDep,
    station_id: str,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[StationDetailResponse]:
    station = await get_station(db, owner_of(user), station_id)
    v = await svc.build_station_view(request.app.state.http, station, coord, db)
    return envelope(
        StationDetailResponse(
            station=v.summary,
            weather=v.current,
            index=v.index,
            trends=svc.build_trend(v.forecast, TrendMetric.RADIATION),
            updated_at=v.current.observed_at if v.current else v.forecast.now().isoformat(),
            basis=v.forecast.basis(),
        ),
        coord,
    )


@router.get("/predictions/station", response_model=Envelope[StationOutlook])
async def station_outlook(
    request: Request,
    user: OptionalUserDep,
    db: DbDep,
    station_id: Annotated[str, Query()],
    days: Annotated[int, Query(ge=1, le=7)] = 7,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[StationOutlook]:
    """未来 7 天逐日预测，首页懒加载。docs/17 §二"""
    import asyncio

    from app.config import settings
    from app.services import prediction

    station = await get_station(db, owner_of(user), station_id)
    # 只读事务在出网前结束，避免慢请求占满连接池。
    await db.commit()
    fc = await weather.station_forecast(request.app.state.http, station)
    out = await asyncio.to_thread(
        prediction.compute_days, station, fc, min(days, settings.forecast_outlook_days)
    )
    from app.services.prediction_archive import save_outlook

    await asyncio.to_thread(save_outlook, station, fc, out)
    return envelope(out, coord)


@router.get("/map/overview", response_model=Envelope[MapOverviewResponse])
async def get_map_overview(
    request: Request,
    user: OptionalUserDep,
    db: DbDep,
    coord: CoordQuery = Coord.WGS84,
    station_id: Annotated[str | None, Query()] = None,
) -> Envelope[MapOverviewResponse]:
    owner = owner_of(user)
    station = (
        await get_station(db, owner, station_id)
        if station_id
        else await svc.default_station(db, owner)
    )
    if station is None:
        from app.errors import StationNotFound

        raise StationNotFound()
    v = await svc.build_station_view(request.app.state.http, station, coord, db)
    return envelope(
        MapOverviewResponse(
            station=v.summary,
            index=v.index,
            weather=v.current,
            ai_hint=svc.outlook_hint(v.forecast, v.index.score),
        ),
        coord,
    )


@router.get("/predictions/fleet", response_model=Envelope[FleetPrediction])
async def fleet_prediction(request: Request, coord: CoordQuery = Coord.WGS84):
    """全目录汇总只含公开目录，游客可看。"""
    from app.services import fleet_prediction as fleet
    from app.weather_model import current_model

    return envelope(await fleet.ensure(request.app.state.http, current_model.get()), coord)


@router.get("/predictions/fleet/history")
async def fleet_history(
    request: Request,
    period: Literal["week", "month", "year"] = "week",
    anchor: date | None = None,
):
    import asyncio
    from datetime import datetime

    from app.errors import ApiError
    from app.services import fleet_history as history
    from app.weather_model import current_model

    if anchor and not 2000 <= anchor.year <= 2100:
        raise ApiError("INVALID_PARAM", "日期超出支持范围", 400)
    result = await asyncio.to_thread(
        history.summary, current_model.get(), period, anchor or datetime.now(history.TZ).date()
    )
    return envelope(result, Coord.WGS84)
