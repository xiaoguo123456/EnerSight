"""首页聚合与趋势。docs/06 §六、§八"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserDep
from app.db import get_session
from app.schemas.common import Coord
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.schemas.home import HomeResponse, TrendMetric, TrendRange, TrendSeries
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
    # 7d 需要更长的预报窗口，属于后续扩展；先按 24h 返回
    return envelope(svc.build_trend(fc, metric), coord)
