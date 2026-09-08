"""把一块网格渲染成 PNG。CPU 密集，调用方放进 executor。docs/05 §6.2"""

import io
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import zoom

from app.render.colormap import SCALES
from app.render.grid import GridData, N

TILE_PX = 512

_TILE_DIR = Path("data/tiles")


def render_png(grid: GridData, layer: str, hour_index: int) -> bytes:
    """17×17 → 512×512，双三次插值后套色阶。

    网格行是南→北，图片行是上→下（北→南），需要上下翻转。
    """
    field = grid.fields[layer][hour_index]  # (N, N)
    filled = np.where(
        np.isnan(field), np.nanmean(field) if not np.all(np.isnan(field)) else 0.0, field
    )
    up = zoom(filled, TILE_PX / N, order=3)[:TILE_PX, :TILE_PX]
    up = np.flipud(up)
    rgba = SCALES[layer].rgba(up)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def tile_path(layer: str, block_key: str, time_key: str) -> Path:
    return _TILE_DIR / layer / block_key / f"{time_key}.png"


def write_tile(path: Path, png: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(png)
    tmp.replace(path)


def tile_dir() -> Path:
    _TILE_DIR.mkdir(parents=True, exist_ok=True)
    return _TILE_DIR
