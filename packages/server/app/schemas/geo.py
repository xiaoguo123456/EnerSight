"""地理服务。docs/06 §十一"""

from typing import Literal

from pydantic import BaseModel

GeoPlaceType = Literal["city", "poi", "station", "coordinate"]


class GeoPlace(BaseModel):
    name: str
    address: str
    latitude: float
    longitude: float
    type: GeoPlaceType


class GeoSearchResponse(BaseModel):
    results: list[GeoPlace]


class GeoReverseResponse(BaseModel):
    address: str
    province: str
    city: str
    district: str
