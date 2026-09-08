"""卫星云图接口。docs/06 §7.3"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserDep
from app.db import get_session
from app.errors import StationNotFound
from app.schemas.common import Coord
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.schemas.satellite import SatelliteCloudResponse
from app.services import satellite as svc
from app.services import weather
from app.services.home import default_station
from app.services.station import get_station

router = APIRouter(prefix="/v1/satellite", tags=["satellite"])
DbDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/cloud", response_model=Envelope[SatelliteCloudResponse])
async def satellite_cloud(
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
    station_id: Annotated[str | None, Query()] = None,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[SatelliteCloudResponse]:
    station = (
        await get_station(db, user.id, station_id)
        if station_id
        else await default_station(db, user.id)
    )
    if station is None:
        raise StationNotFound()
    http = request.app.state.http
    fc = await weather.get_forecast(http, station.latitude, station.longitude)
    base = str(request.base_url).rstrip("/")
    data = await svc.get_cloud(http, station, fc.tz, coord, base)
    return envelope(data, coord)
