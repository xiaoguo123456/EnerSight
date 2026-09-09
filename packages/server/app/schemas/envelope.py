"""响应包装与坐标系参数。docs/06 §2.2、§2.6"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel

from app.schemas.common import Coord
from app.weather_model import current_model


class Meta(BaseModel):
    coord: Coord
    server_time: str
    weather_model: str


class Envelope[T](BaseModel):
    data: T
    meta: Meta


def envelope[T](data: T, coord: Coord) -> Envelope[T]:
    return Envelope(
        data=data,
        meta=Meta(
            coord=coord,
            server_time=datetime.now(UTC).isoformat(),
            weather_model=current_model.get(),
        ),
    )


# 请求参数：coord 默认 wgs84，小程序固定传 gcj02。转换由服务端完成，客户端不做
CoordQuery = Annotated[Coord, Query(description="响应中经纬度的坐标系。小程序传 gcj02")]
