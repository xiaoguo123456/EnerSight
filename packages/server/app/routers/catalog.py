"""公开电站目录接口。docs/06 §5.5

挂在 /v1/stations/catalog 之下；路径要注册在 /v1/stations/{station_id} 之前，
否则 catalog 会被当成 station_id。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserDep
from app.db import get_session
from app.errors import ApiError
from app.schemas.catalog import CatalogSearchResponse
from app.schemas.common import Coord, StationType
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.services import catalog as svc

router = APIRouter(prefix="/v1/stations/catalog", tags=["catalog"])
DbDep = Annotated[AsyncSession, Depends(get_session)]


def _parse_near(near: str | None, coord: Coord) -> tuple[float, float] | None:
    if not near:
        return None
    try:
        lat, lng = (float(x) for x in near.split(","))
    except ValueError as exc:
        raise ApiError("INVALID_PARAM", "near 格式：latitude,longitude", 400) from exc
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ApiError("INVALID_COORDINATE", "near 超出范围", 400)
    return svc.to_wgs84_point(lat, lng, coord)


def _parse_bbox(bbox: str | None, coord: Coord) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError as exc:
        raise ApiError("INVALID_PARAM", "bbox 格式：west,south,east,north", 400) from exc
    s, w = svc.to_wgs84_point(s, w, coord)
    n, e = svc.to_wgs84_point(n, e, coord)
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ApiError("INVALID_PARAM", "bbox 范围非法", 400)
    return w, s, e, n


@router.get("", response_model=Envelope[CatalogSearchResponse])
async def search_catalog(
    user: CurrentUserDep,
    db: DbDep,
    keyword: Annotated[str | None, Query(max_length=64)] = None,
    near: Annotated[str | None, Query(description="latitude,longitude，按 coord")] = None,
    bbox: Annotated[str | None, Query(description="west,south,east,north，按 coord")] = None,
    type: Annotated[StationType | None, Query()] = None,  # noqa: A002
    limit: Annotated[int, Query(ge=1, le=svc.MAX_LIMIT)] = 20,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[CatalogSearchResponse]:
    data = await svc.search(
        db,
        keyword=keyword,
        near=_parse_near(near, coord),
        bbox=_parse_bbox(bbox, coord),
        type_=type,
        coord=coord,
        limit=limit,
    )
    return envelope(data, coord)
