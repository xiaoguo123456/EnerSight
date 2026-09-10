"""图层接口。docs/06 §7.2"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.auth import CurrentUserDep
from app.errors import ApiError
from app.geo import gcj02_to_wgs84
from app.schemas.common import Coord, LayerType
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.schemas.layer import LayerResponse
from app.services import layers as svc

router = APIRouter(prefix="/v1/map/layers", tags=["layers"])


def _parse_bbox(
    bbox: str, coord: Coord, *, convert: bool = True
) -> tuple[float, float, float, float]:
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError as exc:
        raise ApiError("INVALID_PARAM", "bbox 格式：west,south,east,north", 400) from exc
    if convert and coord == Coord.GCJ02:
        w, s = gcj02_to_wgs84(w, s)
        e, n = gcj02_to_wgs84(e, n)
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ApiError("INVALID_PARAM", "bbox 范围非法", 400)
    return w, s, e, n


@router.get("/{layer}", response_model=Envelope[LayerResponse])
async def get_layer(
    request: Request,
    user: CurrentUserDep,
    layer: LayerType,
    bbox: Annotated[str, Query(description="west,south,east,north")],
    zoom: Annotated[int, Query(ge=1, le=20)] = 7,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[LayerResponse]:
    box = _parse_bbox(bbox, coord, convert=layer == LayerType.CLOUD)
    if layer == LayerType.CLOUD and (box[2] - box[0] > 24 or box[3] - box[1] > 24):
        raise ApiError("INVALID_PARAM", "云图视野过大，请放大地图", 400)
    base = str(request.base_url).rstrip("/")
    data = await svc.build_layer(request.app.state.http, layer, box, coord, base)
    return envelope(data, coord)
