"""图层接口。docs/06 §7.2"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from app.errors import ApiError
from app.geo import gcj02_to_wgs84
from app.schemas.common import Coord, LayerType
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.schemas.layer import LayerResponse, MapCloudHistoryResponse
from app.services import layers as svc

router = APIRouter(prefix="/v1/map/layers", tags=["layers"])


@router.get("/cloud/history", response_model=Envelope[MapCloudHistoryResponse])
async def cloud_history(
    request: Request, coord: CoordQuery = Coord.WGS84
) -> Envelope[MapCloudHistoryResponse]:
    return envelope(await svc.satellite_history(request.app.state.http), coord)


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
    layer: LayerType,
    bbox: Annotated[str, Query(description="west,south,east,north")],
    zoom: Annotated[int, Query(ge=1, le=20)] = 7,
    coord: CoordQuery = Coord.WGS84,
    source: Literal["auto", "satellite"] = "auto",
    at: datetime | None = None,
) -> Envelope[LayerResponse]:
    if at is not None and (layer != LayerType.CLOUD or source != "satellite" or at.tzinfo is None):
        raise ApiError("INVALID_PARAM", "at 仅用于带时区的卫星云图历史帧", 400)
    box = _parse_bbox(bbox, coord, convert=layer == LayerType.CLOUD)
    width, height = box[2] - box[0], box[3] - box[1]
    if layer == LayerType.CLOUD and source == "auto" and (width > 24 or height > 24):
        raise ApiError("INVALID_PARAM", "云图视野过大，请放大地图", 400)
    if layer == LayerType.CLOUD and source == "satellite" and (width > 90 or height > 70):
        raise ApiError("INVALID_PARAM", "卫星视野过大，请缩小地区范围", 400)
    base = str(request.base_url).rstrip("/")
    data = await svc.build_layer(
        request.app.state.http, layer, box, coord, base, source,
        at.astimezone(UTC) if at else None,
    )
    return envelope(data, coord)
