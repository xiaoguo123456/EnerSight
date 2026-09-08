"""站点 CRUD。

坐标约定：入参按 request.coord 转成 WGS84 再存；出参按 coord 参数转换。
存储与计算一律 WGS84。docs/06 §2.2
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError, InvalidCoordinate, StationNotFound
from app.geo import gcj02_to_wgs84, wgs84_to_gcj02
from app.models import Station
from app.schemas.common import Coord
from app.schemas.station import (
    CreateStationRequest,
    StationCounts,
    StationListResponse,
    StationMetrics,
    StationSummary,
    UpdateStationRequest,
)


def _to_wgs84(lng: float, lat: float, coord: Coord) -> tuple[float, float]:
    if coord == Coord.GCJ02:
        return gcj02_to_wgs84(lng, lat)
    return lng, lat


def _to_out(lng: float, lat: float, coord: Coord) -> tuple[float, float]:
    if coord == Coord.GCJ02:
        return wgs84_to_gcj02(lng, lat)
    return lng, lat


def to_summary(s: Station, coord: Coord) -> StationSummary:
    lng, lat = _to_out(s.longitude, s.latitude, coord)
    return StationSummary(
        id=s.id,
        name=s.name,
        type=s.type,  # type: ignore[arg-type]
        status=s.status,  # type: ignore[arg-type]
        capacity=s.capacity_kw,
        latitude=lat,
        longitude=lng,
        address=s.address,
        image=s.image,
        # 指标由气象推算，属于 /v1/home 那一步的服务；此处先给 None
        metrics=StationMetrics.empty(),
    )


async def list_stations(
    db: AsyncSession, owner_id: str, coord: Coord, type_: str | None = None
) -> StationListResponse:
    base = select(Station).where(Station.owner_id == owner_id)
    q = base.where(Station.type == type_) if type_ else base
    rows = (await db.execute(q.order_by(Station.created_at))).scalars().all()

    # 指标从逐日累积表读（定时任务每小时写），不在列表里跑 pvlib
    from app.config import settings
    from app.services.accumulate import metrics_from_db

    m = await metrics_from_db(db, [s.id for s in rows])

    # 计数单独算，不从筛选后的列表推 —— 前端 Tab 的数字要对全量。docs/06 §5.1
    count_q = (
        select(Station.type, func.count())
        .where(Station.owner_id == owner_id)
        .group_by(Station.type)
    )
    count_rows = (await db.execute(count_q)).all()
    by_type = {t: n for t, n in count_rows}
    counts = StationCounts(
        all=sum(by_type.values()),
        solar=by_type.get("solar", 0),
        wind=by_type.get("wind", 0),
    )
    stations = []
    for st in rows:
        summary = to_summary(st, coord)
        if st.id in m:
            d = m[st.id]
            summary.metrics = StationMetrics(
                daily_generation=d["daily"],
                current_power=d["current"],
                total_generation=round(d["total"], 1),
                co2_reduction=round(d["total"] * settings.co2_factor_kg_per_kwh, 1),
            )
        stations.append(summary)
    return StationListResponse(stations=stations, counts=counts)


async def get_station(db: AsyncSession, owner_id: str, station_id: str) -> Station:
    s = await db.get(Station, station_id)
    if s is None:
        raise StationNotFound()
    if s.owner_id != owner_id:
        # 不暴露「存在但不是你的」，统一按不存在处理更安全
        raise ApiError("STATION_FORBIDDEN", "无权访问该站点", 403)
    return s


async def create_station(db: AsyncSession, owner_id: str, req: CreateStationRequest) -> Station:
    lng, lat = _to_wgs84(req.longitude, req.latitude, req.coord)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise InvalidCoordinate()
    s = Station(
        owner_id=owner_id,
        name=req.name,
        type=req.type.value,
        latitude=lat,
        longitude=lng,
        capacity_kw=req.capacity,
        tilt=req.tilt,
        azimuth=req.azimuth,
        hub_height=req.hub_height,
        # 逆地理编码填 address 属于 geo 服务，接入腾讯位置服务后补
        address=None,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def update_station(
    db: AsyncSession, owner_id: str, station_id: str, req: UpdateStationRequest
) -> Station:
    s = await get_station(db, owner_id, station_id)
    data = req.model_dump(exclude_unset=True, exclude={"coord"})

    # 经纬度要一起处理，因为坐标转换是二维的
    if "latitude" in data or "longitude" in data:
        lat = data.pop("latitude", s.latitude)
        lng = data.pop("longitude", s.longitude)
        lng, lat = _to_wgs84(lng, lat, req.coord)
        s.latitude, s.longitude = lat, lng

    for k, v in data.items():
        col = "capacity_kw" if k == "capacity" else k
        setattr(s, col, v.value if hasattr(v, "value") else v)

    await db.commit()
    await db.refresh(s)
    return s


async def delete_station(db: AsyncSession, owner_id: str, station_id: str) -> None:
    from app.services.alerts import deactivate_all

    s = await get_station(db, owner_id, station_id)
    await deactivate_all(db, s.id)
    await db.delete(s)
    await db.commit()
