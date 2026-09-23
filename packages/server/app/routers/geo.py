"""地理服务：代理腾讯位置服务，避免在前端暴露 key。docs/06 §十一

游客可用；登录后搜索结果额外包含本人自建场站。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import OptionalUserDep, owner_of
from app.db import get_session
from app.errors import DataUnavailable
from app.geo import gcj02_to_wgs84
from app.schemas.common import Coord
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.schemas.geo import GeoReverseResponse, GeoSearchResponse, ProvinceBoundsResponse
from app.services import geo as svc
from app.services.province_bounds import province_bounds as get_province_bounds

router = APIRouter(prefix="/v1/geo", tags=["geo"])
DbDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/province-bounds", response_model=Envelope[ProvinceBoundsResponse])
async def province_bounds(
    provinces: Annotated[str, Query(min_length=1, max_length=512)],
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[ProvinceBoundsResponse]:
    return envelope(get_province_bounds(provinces, coord), coord)


@router.get("/search", response_model=Envelope[GeoSearchResponse])
async def search(
    request: Request,
    user: OptionalUserDep,
    db: DbDep,
    keyword: Annotated[str, Query(min_length=1, max_length=64)],
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[GeoSearchResponse]:
    data = await svc.search(db, request.app.state.http, owner_of(user), keyword, coord)
    return envelope(data, coord)


@router.get("/reverse", response_model=Envelope[GeoReverseResponse])
async def reverse(
    request: Request,
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
