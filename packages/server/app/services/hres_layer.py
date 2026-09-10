"""IFS HRES 标量贴图与风矢量输出，固定来源，按同一完整起报批次读取。"""

import asyncio
import io
import math
from datetime import datetime

import numpy as np
from PIL import Image

from app.errors import DataUnavailable
from app.geo import wgs84_to_gcj02
from app.render import hres, tiles
from app.render.colormap import SCALES
from app.render.grid import blocks_for_bbox
from app.schemas.common import Coord
from app.schemas.layer import (
    LayerFrame,
    LayerImage,
    LayerResponse,
    Legend,
    ScalarSample,
    WindVector,
)


def scalar(frame, layer):
    if layer == "wind":
        return np.hypot(frame.fields["wind_u_component_10m"], frame.fields["wind_v_component_10m"])
    return frame.fields[hres.FIELDS[layer][0]]


def render(field, layer):
    # 色阶与透明度一起缩放；缺测区域不产生看似有效的温度或零风速。
    valid = np.isfinite(field)
    rgba = SCALES[layer].rgba(np.where(valid, field, 0))
    rgba[~valid, 3] = 0
    im = Image.fromarray(np.flipud(rgba), "RGBA").resize((512, 512), Image.Resampling.BILINEAR)
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def point(lat, lon, coord):
    if coord == Coord.GCJ02:
        lon, lat = wgs84_to_gcj02(lon, lat)
    return {"latitude": lat, "longitude": lon}


async def build(http, layer, bbox, coord, base_url):
    from app.services.layers import _bounds

    name = layer.value
    w, s, e, n = bbox
    span = max(4, math.ceil(max(e - w, n - s) / 4) * 4)
    blocks = blocks_for_bbox(w, s, e, n, span)
    meta = await hres.metadata(http)
    valid = hres.valid_time(meta, name)
    frames = await asyncio.gather(*(hres.fetch(b, name, meta, valid) for b in blocks))
    images, vectors, samples = [], [], []
    for frame in frames:
        b = frame.block
        field = scalar(frame, name)
        if not np.isfinite(field).any():
            raise DataUnavailable()
        rows, cols = field.shape
        run = datetime.fromisoformat(frame.run_at)
        path = tiles.tile_path(f"hres-v1-{name}", b.key, f"{run:%Y%m%dT%H%M}_{valid:%Y%m%dT%H%M}")
        if not path.exists():
            png = await asyncio.to_thread(render, field, name)
            tiles.write_tile(path, png)
        images.append(
            LayerImage(
                url=f"{base_url}/tiles/{path.relative_to(tiles.tile_dir()).as_posix()}",
                bounds=_bounds(b, coord),
            )
        )
        if name == "wind":
            # 按屏幕可见区域抽样输出动画矢量；底色始终来自完整原生网格。
            for lat in np.linspace(max(s, b.lat0), min(n, b.lat1), 25):
                for lon in np.linspace(max(w, b.lon0), min(e, b.lon1), 25):
                    y = min(
                        rows - 1, max(0, round((lat - b.lat0) / (b.lat1 - b.lat0) * (rows - 1)))
                    )
                    x = min(
                        cols - 1, max(0, round((lon - b.lon0) / (b.lon1 - b.lon0) * (cols - 1)))
                    )
                    u, v = (float(frame.fields[k][y, x]) for k in hres.FIELDS[name])
                    if np.isfinite(u) and np.isfinite(v):
                        vectors.append(
                            WindVector(
                                **point(float(lat), float(lon), coord), u=round(u, 3), v=round(v, 3)
                            )
                        )
        else:
            for fy in (1 / 6, 1 / 2, 5 / 6):
                for fx in (1 / 6, 1 / 2, 5 / 6):
                    lat, lon = s + (n - s) * fy, w + (e - w) * fx
                    if not (b.lat0 <= lat < b.lat1 and b.lon0 <= lon < b.lon1):
                        continue
                    y = round((lat - b.lat0) / (b.lat1 - b.lat0) * (rows - 1))
                    x = round((lon - b.lon0) / (b.lon1 - b.lon0) * (cols - 1))
                    value = float(field[y, x])
                    if np.isfinite(value):
                        samples.append(
                            ScalarSample(**point(lat, lon, coord), value=round(value, 1))
                        )
    scale = SCALES[name]
    return LayerResponse(
        layer=layer,
        observed_at=valid.isoformat(),
        unit=scale.unit,
        legend=Legend(
            title=scale.title, type="scale", stops=scale.stops, labels=None, colors=scale.colors
        ),
        frames=[LayerFrame(images=images)],
        frame_interval_ms=None,
        wind_vectors=vectors if name == "wind" else None,
        samples=samples,
        source="ECMWF / Open-Meteo · CC BY 4.0",
        model="IFS HRES",
        resolution_km=9,
        run_at=meta["reference_time"],
    )
