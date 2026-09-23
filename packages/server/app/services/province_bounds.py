"""省级行政区外接矩形，仅用于地图视野定位。docs/06 §十一"""

import json
from functools import lru_cache
from pathlib import Path

from app.errors import ApiError
from app.geo import wgs84_to_gcj02
from app.schemas.common import Coord
from app.schemas.geo import ProvinceBoundsResponse
from app.schemas.layer import Bounds, LatLng

DATA_FILE = Path(__file__).resolve().parents[1] / "geo" / "province_bounds.json"
ALIASES = {"香港特别行政区": "香港", "澳门特别行政区": "澳门"}


@lru_cache(maxsize=1)
def _bounds() -> dict[str, list[float]]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))["bounds"]


def province_bounds(names: str, coord: Coord) -> ProvinceBoundsResponse:
    known = _bounds()
    provinces = list(
        dict.fromkeys(ALIASES.get(x.strip(), x.strip()) for x in names.split(",") if x.strip())
    )
    if not provinces or len(provinces) > len(known) or any(name not in known for name in provinces):
        raise ApiError("INVALID_PARAM", "请选择有效省份", 400)

    w = min(known[name][0] for name in provinces)
    s = min(known[name][1] for name in provinces)
    e = max(known[name][2] for name in provinces)
    n = max(known[name][3] for name in provinces)
    # 开发者工具不支持 includePoints.padding：在服务端留一圈余量，边界不会贴着屏幕裁切。
    lon_pad = max(0.25, (e - w) * 0.15)
    lat_pad = max(0.25, (n - s) * 0.15)
    w, s, e, n = w - lon_pad, s - lat_pad, e + lon_pad, n + lat_pad
    if coord == Coord.GCJ02:
        corners = [wgs84_to_gcj02(lon, lat) for lon in (w, e) for lat in (s, n)]
        w, e = min(x for x, _ in corners), max(x for x, _ in corners)
        s, n = min(y for _, y in corners), max(y for _, y in corners)
    return ProvinceBoundsResponse(
        provinces=provinces,
        bounds=Bounds(sw=LatLng(latitude=s, longitude=w), ne=LatLng(latitude=n, longitude=e)),
    )
