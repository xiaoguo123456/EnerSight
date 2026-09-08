"""公开电站目录接口。docs/06 §5.5"""

from pydantic import BaseModel, Field

from app.schemas.common import StationType


class CatalogPlantOut(BaseModel):
    id: str
    name: str = Field(description="展示名：有中文名用中文名，否则数据集原名")
    name_en: str | None = Field(description="数据集原名（英文），与 name 相同时为 null")
    type: StationType
    capacity: float = Field(description="kW")
    latitude: float = Field(description="已按 coord 转换")
    longitude: float
    address: str | None = Field(description="省市区，未回填为 null")
    owner: str | None
    commissioning_year: int | None
    distance_km: float | None = Field(description="near 查询时到查询点的距离，其余为 null")
    source: str = Field(description="wri | gem，展示出处用")


class CatalogSearchResponse(BaseModel):
    plants: list[CatalogPlantOut]
    total: int = Field(description="目录内该类型总数，供「共收录 N 座」文案")
