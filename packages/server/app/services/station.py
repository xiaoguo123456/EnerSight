"""站点 CRUD。

坐标约定：入参按 request.coord 转成 WGS84 再存；出参按 coord 参数转换。
存储与计算一律 WGS84。docs/06 §2.2
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError, InvalidCoordinate, StationNotFound
from app.geo import gcj02_to_wgs84, wgs84_to_gcj02
from app.models import CatalogPlant, Station
from app.schemas.common import Coord
from app.schemas.station import (
    CreateStationRequest,
    CurtailmentRule,
    PublicStationListResponse,
    StationCounts,
    StationListResponse,
    StationMetrics,
    StationSummary,
    UpdateStationRequest,
)

CATALOG_OWNER = "__catalog__"


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
        is_own=s.owner_id != CATALOG_OWNER,
        curtailment=CurtailmentRule.model_validate(s.curtailment) if s.curtailment else None,
        **getattr(s, "_catalog_metadata", {}),
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
                grid_generation=d["grid"],
            )
        stations.append(summary)
    return StationListResponse(stations=stations, counts=counts)


def from_catalog(p: CatalogPlant) -> Station:
    """公开电站直接参与计算，不复制个人记录，也不加入会话持久化。"""
    from app.services.catalog import join_address

    station = Station(
        id=p.id,
        owner_id=CATALOG_OWNER,
        catalog_id=p.id,
        name=p.display_name,
        type=p.type,
        status="normal",
        latitude=p.latitude,
        longitude=p.longitude,
        capacity_kw=p.capacity_kw,
        address=join_address(p.province, p.city, p.district) or None,
        image=None,
        tilt=None,
        azimuth=None,
        hub_height=None,
    )
    from datetime import UTC

    from app.services.prediction_basis import catalog_basis

    basis, blocked = catalog_basis(p)
    station._pv_capacity = basis
    station._prediction_blocked = blocked
    provenance = p.provenance or {}
    station._catalog_metadata = {
        "phases": provenance.get("phases", []),
        "source_file": provenance.get("source_file"),
        "capacity_note": "原始分期申报容量合计；交流/直流口径见分期"
        if p.type == "solar"
        else "已投运分期额定容量合计",
        "prediction_blocked_reason": blocked,
        "source": p.source,
        "original_name": p.name,
        "local_name": p.name_local,
        "catalog_updated_at": p.updated_at.replace(tzinfo=UTC).isoformat()
        if p.updated_at
        else None,
        "owner_name": p.owner_name,
    }
    return station


async def list_public_stations(
    db: AsyncSession,
    coord: Coord,
    type_: str | None,
    keyword: str,
    limit: int,
    offset: int,
    province: str = "",
    sort: str = "capacity",
) -> PublicStationListResponse:
    active = CatalogPlant.status == "operating"
    count_rows = (
        await db.execute(
            select(CatalogPlant.type, func.count()).where(active).group_by(CatalogPlant.type)
        )
    ).all()
    counts = dict(count_rows)
    filters = [active]
    if province:
        filters.append(CatalogPlant.province == province)
    if type_:
        filters.append(CatalogPlant.type == type_)
    if keyword.strip():
        filters.append(
            or_(
                *[
                    col.icontains(keyword.strip(), autoescape=True)
                    for col in (
                        CatalogPlant.name,
                        CatalogPlant.name_local,
                        CatalogPlant.province,
                        CatalogPlant.city,
                        CatalogPlant.district,
                        CatalogPlant.owner_name,
                    )
                ]
            )
        )
    total = (
        await db.execute(select(func.count()).select_from(CatalogPlant).where(*filters))
    ).scalar_one()
    plants = (
        (
            await db.execute(
                select(CatalogPlant)
                .where(*filters)
                .order_by(
                    func.coalesce(CatalogPlant.name_local, CatalogPlant.name)
                    if sort == "name"
                    else CatalogPlant.capacity_kw.desc(),
                    CatalogPlant.id,
                )
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    regions = (
        (
            await db.execute(
                select(CatalogPlant.province)
                .where(active, CatalogPlant.province.is_not(None), CatalogPlant.province != "")
                .distinct()
                .order_by(CatalogPlant.province)
            )
        )
        .scalars()
        .all()
    )
    return PublicStationListResponse(
        stations=[to_summary(from_catalog(p), coord) for p in plants],
        counts=StationCounts(
            all=sum(counts.values()), solar=counts.get("solar", 0), wind=counts.get("wind", 0)
        ),
        regions=list(regions),
        total=total,
        has_more=offset + len(plants) < total,
    )


async def get_station(db: AsyncSession, owner_id: str, station_id: str) -> Station:
    plant = await db.get(CatalogPlant, station_id)
    if plant is not None:
        if plant.status != "operating":
            raise StationNotFound()
        return from_catalog(plant)
    s = await db.get(Station, station_id)
    if s is None:
        raise StationNotFound()
    if s.owner_id != owner_id:
        # 不暴露「存在但不是你的」，统一按不存在处理更安全
        raise ApiError("STATION_FORBIDDEN", "无权访问该站点", 403)
    return s


async def _check_limit(db: AsyncSession, owner_id: str) -> None:
    """每用户自建上限。docs/17 §一"""
    from app.config import settings

    count = (
        await db.execute(
            select(func.count()).select_from(Station).where(Station.owner_id == owner_id)
        )
    ).scalar_one()
    if count >= settings.max_stations_per_user:
        raise ApiError(
            "STATION_LIMIT", f"最多添加 {settings.max_stations_per_user} 座场站", 400
        )


async def create_station(
    db: AsyncSession, owner_id: str, req: CreateStationRequest, http=None
) -> Station:
    from app.models import CatalogPlant

    plant: CatalogPlant | None = None
    if req.catalog_id:
        plant = await db.get(CatalogPlant, req.catalog_id)
        if plant is None:
            raise ApiError("CATALOG_NOT_FOUND", "公开电站不存在", 404)
        # 幂等：同一用户重复添加同一座，返回已有的
        existing = (
            await db.execute(
                select(Station).where(
                    Station.owner_id == owner_id, Station.catalog_id == req.catalog_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    await _check_limit(db, owner_id)
    if req.latitude is not None and req.longitude is not None:
        lng, lat = _to_wgs84(req.longitude, req.latitude, req.coord)
    elif plant is not None:
        lng, lat = plant.longitude, plant.latitude
    else:
        raise ApiError("INVALID_PARAM", "缺少经纬度，或给 catalog_id", 400)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise InvalidCoordinate()
    name = req.name or (plant.display_name if plant else None)
    type_ = req.type.value if req.type else (plant.type if plant else None)
    capacity = req.capacity or (plant.capacity_kw if plant else None)
    if not name or not type_ or not capacity:
        raise ApiError("INVALID_PARAM", "name / type / capacity 必填，或给 catalog_id", 400)
    from app.services.catalog import join_address

    address = join_address(plant.province, plant.city, plant.district) if plant else None

    s = Station(
        owner_id=owner_id,
        name=name[:64],
        type=type_,
        latitude=lat,
        longitude=lng,
        capacity_kw=capacity,
        tilt=req.tilt,
        azimuth=req.azimuth,
        hub_height=req.hub_height,
        curtailment=req.curtailment.model_dump() if req.curtailment else None,
        address=address or None,
        catalog_id=req.catalog_id,
    )
    if http is not None and s.address is None:
        # 逆地理编码 best effort：失败不阻塞建站，定时任务会补
        from app.services.geo import reverse

        try:
            r = await reverse(http, lat, lng)
            if r:
                s.address = r.address
        except Exception:  # noqa: BLE001
            pass
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def update_station(
    db: AsyncSession, owner_id: str, station_id: str, req: UpdateStationRequest
) -> Station:
    s = await get_station(db, owner_id, station_id)
    if s.owner_id == "__catalog__":
        raise ApiError("CATALOG_READ_ONLY", "公开电站由平台维护，不支持个人修改或删除", 403)
    data = req.model_dump(exclude_unset=True, exclude={"coord"})
    # 出力约束：传 null 清除，不传保持；已经被 model_dump 展开成 dict
    if "curtailment" in data:
        s.curtailment = data.pop("curtailment")

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
    if s.owner_id == "__catalog__":
        raise ApiError("CATALOG_READ_ONLY", "公开电站由平台维护，不支持个人修改或删除", 403)
    await deactivate_all(db, s.id)
    await db.delete(s)
    await db.commit()
