"""地理服务：代理腾讯位置服务，避免在前端暴露 key。docs/06 §十一"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserDep
from app.db import get_session
from app.errors import DataUnavailable
from app.geo import gcj02_to_wgs84
from app.schemas.common import Coord
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.schemas.geo import GeoReverseResponse, GeoSearchResponse
from app.services import geo as svc

router = APIRouter(prefix="/v1/geo", tags=["geo"])
DbDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/search", response_model=Envelope[GeoSearchResponse])
async def search(
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
    keyword: Annotated[str, Query(min_length=1, max_length=64)],
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[GeoSearchResponse]:
    return envelope(await svc.search(db, request.app.state.http, user.id, keyword, coord), coord)


@router.get("/reverse", response_model=Envelope[GeoReverseResponse])
async def reverse(
    request: Request,
    user: CurrentUserDep,
    latitude: Annotated[float, Query(ge=-90, le=90)],
    longitude: Annotated[float, Query(ge=-180, le=180)],
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[GeoReverseResponse]:
    # 入参坐标系按 coord；内部一律 WGS84
    if coord == Coord.GCJ02:
        longitude, latitude = gcj02_to_wgs84(longitude, latitude)
    r = await svc.reverse(request.app.state.http, latitude, longitude)
    if r is None:
        raise DataUnavailable("该位置暂无地址信息")
    return envelope(r, coord)
