"""地理服务：逆地理编码、搜索。docs/06 §十一"""

import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import AsyncTTLCache, grid_key
from app.models import Station
from app.providers.tencent_lbs import TencentLBS
from app.schemas.common import Coord
from app.schemas.geo import GeoPlace, GeoReverseResponse, GeoSearchResponse
from app.services.station import _to_out

# 地址按 0.01° 网格（约 1km）缓存一天：行政区划几乎不变
_reverse_cache = AsyncTTLCache(maxsize=4096, ttl_seconds=24 * 3600)

# 两个数都允许 1–3 位整数：用户可能把经度写在前面，交给下面按范围纠正
_COORD_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,，\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


async def reverse(
    http: httpx.AsyncClient, latitude: float, longitude: float
) -> GeoReverseResponse | None:
    key = grid_key(latitude, longitude, step=0.01)

    async def _load():
        r = await TencentLBS(http).reverse(latitude, longitude)
        return r or {}

    r = await _reverse_cache.get_or_load(key, _load)
    if not r:
        return None
    return GeoReverseResponse(**r)


def parse_coordinate(text: str) -> tuple[float, float] | None:
    """「31.30, 120.62」→ (lat, lng)。用户可能纬度经度写反，按数值范围判断。"""
    m = _COORD_RE.match(text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    # 中国境内纬度 < 经度；若 a > 90 必是经度
    if abs(a) > 90 and abs(b) <= 90:
        a, b = b, a
    if not (-90 <= a <= 90 and -180 <= b <= 180):
        return None
    return a, b


async def search(
    db: AsyncSession, http: httpx.AsyncClient, owner_id: str, keyword: str, coord: Coord
) -> GeoSearchResponse:
    """合并三类结果：用户站点匹配、坐标直解、城市 POI。docs/06 §十一"""
    results: list[GeoPlace] = []
    kw = keyword.strip()
    if not kw:
        return GeoSearchResponse(results=[])

    # 1. 用户站点
    rows = (
        (
            await db.execute(
                select(Station).where(Station.owner_id == owner_id, Station.name.contains(kw))
            )
        )
        .scalars()
        .all()
    )
    for s in rows:
        lng, lat = _to_out(s.longitude, s.latitude, coord)
        results.append(
            GeoPlace(
                name=s.name, address=s.address or "", latitude=lat, longitude=lng, type="station"
            )
        )

    # 2. 坐标直解（输入视为 WGS84）
    pc = parse_coordinate(kw)
    if pc:
        lng, lat = _to_out(pc[1], pc[0], coord)
        results.append(
            GeoPlace(
                name=f"坐标 {pc[0]:.4f}, {pc[1]:.4f}",
                address="按经纬度定位",
                latitude=lat,
                longitude=lng,
                type="coordinate",
            )
        )

    # 3. 城市 / POI
    for item in await TencentLBS(http).suggest(kw):
        lng, lat = _to_out(item["longitude"], item["latitude"], coord)
        results.append(
            GeoPlace(
                name=item["name"],
                address=item["address"],
                latitude=lat,
                longitude=lng,
                type="poi",
            )
        )

    return GeoSearchResponse(results=results[:12])
