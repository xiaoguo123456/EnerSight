"""地理服务。docs/06 §十一"""

from typing import Literal

from pydantic import BaseModel, Field

# plant = 公开电站目录里的场站，可一键添加为自己的站点
GeoPlaceType = Literal["city", "poi", "station", "coordinate", "plant"]


class GeoPlace(BaseModel):
    name: str
    address: str
    latitude: float
    longitude: float
    type: GeoPlaceType
    catalog_id: str | None = Field(description="type=plant 时为目录 id，其余 null")


class GeoSearchResponse(BaseModel):
    results: list[GeoPlace]


class GeoReverseResponse(BaseModel):
    address: str
    province: str
    city: str
    district: str
