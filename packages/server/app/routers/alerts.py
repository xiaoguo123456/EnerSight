"""预警接口。docs/06 §九

游客可看公开电站的预警；自建场站由 get_station 要求登录。docs/09 §4.3
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import OptionalUserDep, owner_of
from app.db import get_session
from app.errors import ApiError, StationNotFound
from app.schemas.alert import AlertListResponse, CurrentAlertResponse
from app.schemas.common import Coord
from app.schemas.envelope import CoordQuery, Envelope, envelope
from app.services import alerts as svc
from app.services import satellite, weather
from app.services.home import default_station
from app.services.station import get_station, to_summary

router = APIRouter(prefix="/v1/alerts", tags=["alerts"])
DbDep = Annotated[AsyncSession, Depends(get_session)]
LEVELS = {"all", "minor", "moderate", "severe"}


async def _resolve(db: AsyncSession, owner_id: str | None, station_id: str | None):
    station = (
        await get_station(db, owner_id, station_id)
        if station_id
        else await default_station(db, owner_id)
    )
    if station is None:
        raise StationNotFound()
    # 出网前归还连接；预警页并发的两个请求不能占满测试环境的连接池。
    await db.commit()
    return station


@router.get("", response_model=Envelope[AlertListResponse])
async def list_alerts(
    request: Request,
    user: OptionalUserDep,
    db: DbDep,
    station_id: Annotated[str | None, Query()] = None,
    level: Annotated[str, Query()] = "all",
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[AlertListResponse]:
    if level not in LEVELS:
        raise ApiError("INVALID_PARAM", "level 取值：all / minor / moderate / severe", 400)
    station = await _resolve(db, owner_of(user), station_id)
    fc = await weather.station_forecast(request.app.state.http, station)
    before = datetime.fromisoformat(cursor) if cursor else None
    items, next_cursor = await svc.list_alerts(db, station.id, fc.tz, level, limit, before)
    return envelope(
        AlertListResponse(
            alerts=items, next_cursor=next_cursor.isoformat() if next_cursor else None
        ),
        coord,
    )


@router.get("/current", response_model=Envelope[CurrentAlertResponse])
async def current_alert(
    request: Request,
    user: OptionalUserDep,
    db: DbDep,
    station_id: Annotated[str | None, Query()] = None,
    coord: CoordQuery = Coord.WGS84,
) -> Envelope[CurrentAlertResponse]:
    station = await _resolve(db, owner_of(user), station_id)
    http = request.app.state.http
    fc = await weather.station_forecast(http, station)
    base = str(request.base_url).rstrip("/")
    # 卫星拿不到（夜间、上游故障）不影响预报类预警，云图置 null
    sat = await satellite.load_scene_safely(http, station, base)
    scene = sat.scene
    # 读请求顺手扫一次，保证首次访问就有结果；定时任务负责常态刷新
    await svc.scan_station(db, station, fc, sat)
    await db.commit()
    alert = await svc.current_alert(db, station.id, fc.tz)
    cloud_motion = None
    if scene and alert and alert.source == "satellite":
        est = satellite.estimate_motion(scene, station)
        if est:
            cloud_motion = satellite.to_cloud_motion(est, station, scene.observed_at, fc.tz)
    return envelope(
        CurrentAlertResponse(
            station=to_summary(station, coord),
            checked_at=datetime.now(UTC).isoformat(),
            alert=alert,
            cloud_motion=cloud_motion,
            satellite=satellite.to_response(scene, station, fc.tz, coord) if scene else None,
            satellite_status=sat.status,
        ),
        coord,
    )
