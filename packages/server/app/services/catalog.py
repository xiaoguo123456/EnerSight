"""公开电站目录：搜索、附近、视野内。docs/06 §5.5

目录只读，写入只有 scripts/import_catalog.py。
"""

import math

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.geo import gcj02_to_wgs84, wgs84_to_gcj02
from app.models import CatalogPlant
from app.schemas.catalog import CatalogPlantOut, CatalogSearchResponse
from app.schemas.common import Coord, StationType

MAX_LIMIT = 200


def _out(p: CatalogPlant, coord: Coord, distance_km: float | None = None) -> CatalogPlantOut:
    lng, lat = p.longitude, p.latitude
    if coord == Coord.GCJ02:
        lng, lat = wgs84_to_gcj02(lng, lat)
    parts = [x for x in (p.province, p.city, p.district) if x]
    return CatalogPlantOut(
        id=p.id,
        name=p.display_name,
        name_en=p.name if p.name_local else None,
        type=StationType(p.type),
        capacity=p.capacity_kw,
        latitude=lat,
        longitude=lng,
        address="".join(parts) or None,
        owner=p.owner_name,
        commissioning_year=p.commissioning_year,
        distance_km=round(distance_km, 1) if distance_km is not None else None,
        source=p.source,
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def count(db: AsyncSession, type_: StationType | None) -> int:
    q = select(func.count()).select_from(CatalogPlant)
    if type_:
        q = q.where(CatalogPlant.type == type_.value)
    return int((await db.execute(q)).scalar_one())


async def search(
    db: AsyncSession,
    *,
    keyword: str | None,
    near: tuple[float, float] | None,
    bbox: tuple[float, float, float, float] | None,
    type_: StationType | None,
    coord: Coord,
    limit: int,
) -> CatalogSearchResponse:
    """三种查法二选一优先级：keyword > near > bbox；都不给返回容量最大的前 limit 座。

    near / bbox 入参已按 coord 转成 WGS84。
    """
    limit = min(limit, MAX_LIMIT)
    q = select(CatalogPlant)
    if type_:
        q = q.where(CatalogPlant.type == type_.value)
    total = await count(db, type_)

    if keyword and keyword.strip():
        kw = keyword.strip()
        q = (
            q.where(
                or_(
                    CatalogPlant.name_local.contains(kw),
                    CatalogPlant.name.contains(kw),
                    CatalogPlant.province.contains(kw),
                    CatalogPlant.city.contains(kw),
                    CatalogPlant.district.contains(kw),
                    CatalogPlant.owner_name.contains(kw),
                )
            )
            .order_by(CatalogPlant.capacity_kw.desc())
            .limit(limit)
        )
        rows = (await db.execute(q)).scalars().all()
        return CatalogSearchResponse(plants=[_out(p, coord) for p in rows], total=total)

    if near:
        lat, lng = near
        # 先用 ±2° 的经纬度框粗筛（约 200 km），再精确算距离排序
        span = 2.0
        q = q.where(
            CatalogPlant.latitude.between(lat - span, lat + span),
            CatalogPlant.longitude.between(lng - span, lng + span),
        )
        rows = (await db.execute(q)).scalars().all()
        with_d = sorted(
            ((haversine_km(lat, lng, p.latitude, p.longitude), p) for p in rows),
            key=lambda t: t[0],
        )[:limit]
        return CatalogSearchResponse(plants=[_out(p, coord, d) for d, p in with_d], total=total)

    if bbox:
        w, s, e, n = bbox
        q = (
            q.where(CatalogPlant.latitude.between(s, n), CatalogPlant.longitude.between(w, e))
            .order_by(CatalogPlant.capacity_kw.desc())
            .limit(limit)
        )
        rows = (await db.execute(q)).scalars().all()
        return CatalogSearchResponse(plants=[_out(p, coord) for p in rows], total=total)

    rows = (
        (await db.execute(q.order_by(CatalogPlant.capacity_kw.desc()).limit(limit))).scalars().all()
    )
    return CatalogSearchResponse(plants=[_out(p, coord) for p in rows], total=total)


async def get(db: AsyncSession, plant_id: str) -> CatalogPlant | None:
    return await db.get(CatalogPlant, plant_id)


def to_wgs84_point(lat: float, lng: float, coord: Coord) -> tuple[float, float]:
    if coord == Coord.GCJ02:
        lng, lat = gcj02_to_wgs84(lng, lat)
    return lat, lng
