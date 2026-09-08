"""AI 分析报告。docs/06 §十"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserDep
from app.db import get_session
from app.schemas.common import Coord
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.schemas.report import AIReportResponse
from app.services import reports as svc
from app.services import weather
from app.services.station import get_station, to_summary

router = APIRouter(prefix="/v1/reports", tags=["reports"])
DbDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{station_id}", response_model=Envelope[AIReportResponse])
async def get_report(
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
    station_id: str,
    date_: Annotated[date | None, Query(alias="date")] = None,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[AIReportResponse]:
    station = await get_station(db, user.id, station_id)
    row = await svc.get_or_generate(db, request.app.state.http, station, date_)
    fc = await weather.get_forecast(request.app.state.http, station.latitude, station.longitude)
    return envelope(svc.to_response(row, to_summary(station, coord), fc.tz), coord)
