"""站点接口。docs/06 §五"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserDep
from app.db import get_session
from app.schemas.common import Coord, StationType
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.schemas.station import (
    CreateStationRequest,
    StationListResponse,
    StationSummary,
    UpdateStationRequest,
)
from app.services import station as svc

router = APIRouter(prefix="/v1/stations", tags=["stations"])
DbDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=Envelope[StationListResponse])
async def list_stations(
    user: CurrentUserDep,
    db: DbDep,
    coord: CoordQuery = Coord.WGS84,
    type: Annotated[StationType | None, Query()] = None,
) -> Envelope[StationListResponse]:
    data = await svc.list_stations(db, user.id, coord, type.value if type else None)
    return envelope(data, coord)


@router.post("", response_model=Envelope[StationSummary], status_code=201)
async def create_station(
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
    body: CreateStationRequest,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[StationSummary]:
    s = await svc.create_station(db, user.id, body, request.app.state.http)
    return envelope(svc.to_summary(s, coord), coord)


@router.patch("/{station_id}", response_model=Envelope[StationSummary])
async def update_station(
    user: CurrentUserDep,
    db: DbDep,
    station_id: str,
    body: UpdateStationRequest,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[StationSummary]:
    s = await svc.update_station(db, user.id, station_id, body)
    return envelope(svc.to_summary(s, coord), coord)


@router.delete("/{station_id}", status_code=204)
async def delete_station(user: CurrentUserDep, db: DbDep, station_id: str) -> Response:
    await svc.delete_station(db, user.id, station_id)
    return Response(status_code=204)
