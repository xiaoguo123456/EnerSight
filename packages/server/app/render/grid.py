"""气象网格：按 4°×4° 块拉取 Open-Meteo 多点数据并缓存。docs/05 §6.4

块边界对齐到 4° 的整数倍，相邻用户请求命中同一块。
一块 17×17 = 289 点（0.25°），Open-Meteo 单次请求可承受。
"""

import itertools
from dataclasses import dataclass

import httpx
import numpy as np

from app.cache import AsyncTTLCache
from app.config import settings
from app.errors import UpstreamUnavailable
from app.render.colormap import FIELD

BLOCK_DEG = 4.0
# 0.5°：一块 9×9 = 81 点。0.25° 的 289 点在多块并发时会触发 Open-Meteo 限流，
# 而 512px 插值下 0.5° 与 0.25° 视觉无差。
STEP_DEG = 0.5
N = int(BLOCK_DEG / STEP_DEG) + 1  # 9

_cache = AsyncTTLCache(maxsize=256, ttl_seconds=settings.ttl_hourly_forecast)


@dataclass(frozen=True)
class Block:
    """左下角对齐到 4° 整数倍的块。"""

    lat0: float
    lon0: float

    @property
    def lat1(self) -> float:
        return self.lat0 + BLOCK_DEG

    @property
    def lon1(self) -> float:
        return self.lon0 + BLOCK_DEG

    @property
    def key(self) -> str:
        return f"{self.lat0:+06.1f}_{self.lon0:+07.1f}"

    @staticmethod
    def containing(lat: float, lon: float) -> "Block":
        import math

        return Block(
            math.floor(lat / BLOCK_DEG) * BLOCK_DEG, math.floor(lon / BLOCK_DEG) * BLOCK_DEG
        )


def blocks_for_bbox(w: float, s: float, e: float, n: float) -> list[Block]:
    """覆盖 bbox 的全部块。"""
    import math

    out = []
    lat = math.floor(s / BLOCK_DEG) * BLOCK_DEG
    while lat < n:
        lon = math.floor(w / BLOCK_DEG) * BLOCK_DEG
        while lon < e:
            out.append(Block(lat, lon))
            lon += BLOCK_DEG
        lat += BLOCK_DEG
    return out


@dataclass(frozen=True)
class GridData:
    """一块 24 小时的四个场，形状 (24, N, N)，行是纬度（南→北），列是经度（西→东）。"""

    block: Block
    times: list[str]  # UTC ISO
    fields: dict[str, np.ndarray]


async def fetch_block(http: httpx.AsyncClient, block: Block) -> GridData:
    async def _load() -> GridData:
        lats = [round(block.lat0 + i * STEP_DEG, 4) for i in range(N)]
        lons = [round(block.lon0 + j * STEP_DEG, 4) for j in range(N)]
        pts = list(itertools.product(lats, lons))  # 行优先：lat 外层
        params = {
            "latitude": ",".join(str(p[0]) for p in pts),
            "longitude": ",".join(str(p[1]) for p in pts),
            "hourly": ",".join(sorted(set(FIELD.values()))),
            "forecast_days": 1,
            "timezone": "UTC",
            "wind_speed_unit": "ms",
        }
        try:
            res = await http.get(f"{settings.open_meteo_base}/forecast", params=params, timeout=30)
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable() from exc
        if res.status_code == 429:
            raise UpstreamUnavailable("气象服务限流，请稍后重试")
        if res.status_code >= 400:
            raise UpstreamUnavailable()
        arr = res.json()
        if not isinstance(arr, list) or len(arr) != N * N:
            raise UpstreamUnavailable("网格数据不完整")

        times = arr[0]["hourly"]["time"]
        fields: dict[str, np.ndarray] = {}
        for layer, field in FIELD.items():
            cube = np.full((len(times), N, N), np.nan, dtype=float)
            for idx, item in enumerate(arr):
                i, j = divmod(idx, N)
                vals = item["hourly"].get(field) or []
                cube[: len(vals), i, j] = [v if v is not None else np.nan for v in vals]
            fields[layer] = cube
        return GridData(block=block, times=times, fields=fields)

    return await _cache.get_or_load(f"grid:{block.key}", _load)


def clear_cache() -> None:
    _cache.clear()
