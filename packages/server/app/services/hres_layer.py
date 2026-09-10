"""IFS HRES 地图读取已准备的栅格，通过本地工作进程输出固定瓦片。"""

from datetime import UTC, datetime, timedelta

from app.errors import ApiError
from app.jobs import map_prepare
from app.render import map_raster
from app.render.colormap import SCALES
from app.schemas.layer import (
    LayerFrame,
    LayerImage,
    LayerResponse,
    Legend,
    ScalarSample,
    WindVector,
)


async def build(http, layer, bbox, coord, base_url):
    name = layer.value
    region = map_raster.intersect(bbox)
    if region is None:
        raise ApiError("MAP_OUTSIDE_COVERAGE", "气象图层覆盖中国及周边地区", 400)
    now = datetime.now(UTC)
    target = now.replace(minute=0, second=0, microsecond=0)
    if name == "radiation" and now > target:
        target += timedelta(hours=1)
    found = map_raster.available(map_prepare.data_dir(), name, target)
    if found is None:
        raise ApiError("MAP_PREPARING", "气象图层正在后台准备，请稍后刷新", 503)
    manifest, record = found
    images, points = await map_prepare.render(
        map_prepare.data_dir(),
        str(manifest),
        record,
        coord.value,
        region,
    )
    scale = SCALES[name]
    return LayerResponse(
        layer=layer,
        observed_at=record["valid_at"],
        unit=scale.unit,
        legend=Legend(
            title=scale.title, type="scale", stops=scale.stops, labels=None, colors=scale.colors
        ),
        frames=[
            LayerFrame(
                images=[
                    LayerImage(
                        url=f"{base_url}/tiles/{path}",
                        bounds={
                            "sw": {"longitude": w, "latitude": s},
                            "ne": {"longitude": e, "latitude": n},
                        },
                    )
                    for path, (w, s, e, n) in images
                ]
            )
        ],
        frame_interval_ms=None,
        wind_vectors=[WindVector(longitude=x, latitude=y, u=v[0], v=v[1]) for x, y, v in points]
        if name == "wind"
        else None,
        samples=[ScalarSample(longitude=x, latitude=y, value=round(v[0], 1)) for x, y, v in points]
        if name != "wind"
        else [],
        source="ECMWF / Open-Meteo · CC BY 4.0",
        model="IFS HRES",
        resolution_km=9,
        run_at=record["run_at"],
        stale=datetime.fromisoformat(record["valid_at"]) != target,
        coverage="中国及周边 · 温度等值线间隔 2℃" if name == "temperature" else "中国及周边",
    )
