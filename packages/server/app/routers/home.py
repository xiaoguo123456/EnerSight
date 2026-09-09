"""首页聚合与趋势。docs/06 §六、§八"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserDep
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
from app.schemas.prediction import FleetPrediction
from app.services import home as svc
from app.services import weather
from app.services.station import get_station

router = APIRouter(prefix="/v1", tags=["home"])
DbDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/home", response_model=Envelope[HomeResponse])
async def get_home(
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
    coord: CoordQuery = Coord.WGS84,
    station_id: Annotated[str | None, Query()] = None,
) -> Envelope[HomeResponse]:
    data = await svc.build_home(db, request.app.state.http, user.id, coord, station_id)
    return envelope(data, coord)


@router.get("/trends", response_model=Envelope[TrendSeries])
async def get_trends(
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
    station_id: Annotated[str, Query()],
    metric: Annotated[TrendMetric, Query()] = TrendMetric.RADIATION,
    range_: Annotated[TrendRange, Query(alias="range")] = TrendRange.H24,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[TrendSeries]:
    station = await get_station(db, user.id, station_id)
    fc = await weather.get_forecast(request.app.state.http, station.latitude, station.longitude)
    return envelope(svc.build_trend(fc, metric, range_), coord)


@router.get("/stations/{station_id}/detail", response_model=Envelope[StationDetailResponse])
async def get_station_detail(
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
    station_id: str,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[StationDetailResponse]:
    station = await get_station(db, user.id, station_id)
    v = await svc.build_station_view(request.app.state.http, station, coord, db)
    return envelope(
        StationDetailResponse(
            station=v.summary,
            weather=v.current,
            index=v.index,
            trends=svc.build_trend(v.forecast, TrendMetric.RADIATION),
            updated_at=v.current.observed_at if v.current else v.forecast.now().isoformat(),
        ),
        coord,
    )


@router.get("/map/overview", response_model=Envelope[MapOverviewResponse])
async def get_map_overview(
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
    coord: CoordQuery = Coord.WGS84,
    station_id: Annotated[str | None, Query()] = None,
) -> Envelope[MapOverviewResponse]:
    station = (
        await get_station(db, user.id, station_id)
        if station_id
        else await svc.default_station(db, user.id)
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
async def fleet_prediction(request: Request, user: CurrentUserDep, coord: CoordQuery = Coord.WGS84):
    from app.services import fleet_prediction as fleet
    from app.weather_model import current_model

    return envelope(await fleet.ensure(request.app.state.http, current_model.get()), coord)
