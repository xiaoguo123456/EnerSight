"""云量降级网格：按 4°×4° 块拉取 Open-Meteo 多点数据并缓存。docs/05 §6.4

温度、风场和辐射地图已改由 hres.py 读取原生空间数据。

块边界对齐到 4° 的整数倍，相邻用户请求命中同一块。
每块 5×5 点；大视野扩大块范围，避免缩放拉取大量密集采样。
"""

import asyncio
import itertools
import time
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime

import httpx
import numpy as np

from app.cache import AsyncTTLCache
from app.config import settings
from app.errors import ApiError, UpstreamUnavailable
from app.render.colormap import FIELD
from app.weather_model import current_model

BLOCK_DEG = 4.0
# 固定 5×5 采样，大范围只提供粗分辨率气象参考。
STEP_DEG = 1.0
N = int(BLOCK_DEG / STEP_DEG) + 1  # 5

_cache = AsyncTTLCache(maxsize=256, ttl_seconds=settings.ttl_hourly_forecast)


@dataclass(frozen=True)
class Block:
    """左下角对齐到 4° 整数倍的块。"""

    lat0: float
    lon0: float
    span: float = BLOCK_DEG

    @property
    def lat1(self) -> float:
        return min(90, self.lat0 + self.span)

    @property
    def lon1(self) -> float:
        return min(180, self.lon0 + self.span)

    @property
    def key(self) -> str:
        return f"{self.lat0:+06.1f}_{self.lon0:+07.1f}_{self.span:g}d_v2"

    @staticmethod
    def containing(lat: float, lon: float) -> "Block":
        import math

        return Block(
            math.floor(lat / BLOCK_DEG) * BLOCK_DEG, math.floor(lon / BLOCK_DEG) * BLOCK_DEG
        )


def blocks_for_bbox(w: float, s: float, e: float, n: float, span: float = BLOCK_DEG) -> list[Block]:
    """覆盖 bbox 的全部块。"""
    import math

    out = []
    lat = max(-90, math.floor(s / span) * span)
    while lat < n:
        lon = max(-180, math.floor(w / span) * span)
        while lon < e:
            out.append(Block(lat, lon, span))
            lon += span
        lat += span
    return out


@dataclass(frozen=True)
class GridData:
    """一块 24 小时的四个场，形状 (24, N, N)，行是纬度（南→北），列是经度（西→东）。"""

    block: Block
    times: list[str]  # UTC ISO
    fields: dict[str, np.ndarray]
    fetched_at: float = dataclass_field(default_factory=time.time)


_gate = asyncio.Lock()
_cooldown_until = 0.0


async def fetch_block(http: httpx.AsyncClient, block: Block) -> GridData:
    async def _load() -> GridData:
        global _cooldown_until
        # 持久化当日预报，发布重启不丢失已获取的网格；超过 1 小时才刷新。
        from app.render import tiles

        path = (
            tiles.tile_dir().parent
            / "grid-cache-v4"
            / f"{current_model.get()}_{block.key}_{datetime.now(UTC):%Y%m%d}.npz"
        )
        cached = None
        if path.exists():
            try:
                with np.load(path, allow_pickle=False) as z:
                    cached = GridData(
                        block,
                        z["times"].tolist(),
                        {k: z[k] for k in z.files if k != "times"},
                        path.stat().st_mtime,
                    )
                if time.time() - path.stat().st_mtime < 3600:
                    return cached
            except (OSError, ValueError):
                cached = None

        def limited():
            return ApiError("WEATHER_RATE_LIMITED", "气象服务冷却中，请约一分钟后重试", 429)

        if time.monotonic() < _cooldown_until:
            if cached is not None:
                return cached
            raise limited()
        lats = [round(block.lat0 + i * (block.lat1 - block.lat0) / (N - 1), 4) for i in range(N)]
        lons = [round(block.lon0 + j * (block.lon1 - block.lon0) / (N - 1), 4) for j in range(N)]
        pts = list(itertools.product(lats, lons))  # 行优先：lat 外层
        params = {
            "latitude": ",".join(str(p[0]) for p in pts),
            "longitude": ",".join(str(p[1]) for p in pts),
            "hourly": ",".join(sorted(set(FIELD.values()) | {"wind_direction_10m"})),
            "forecast_days": 2,
            "models": current_model.get(),
            "timezone": "UTC",
            "wind_speed_unit": "ms",
        }
        try:
            async with _gate:
                if time.monotonic() < _cooldown_until:
                    if cached is not None:
                        return cached
                    raise limited()
                res = await http.get(
                    f"{settings.open_meteo_base}/forecast", params=params, timeout=25
                )
                if res.status_code == 429:
                    _cooldown_until = time.monotonic() + 60

        except httpx.HTTPError as exc:
            raise UpstreamUnavailable() from exc
        if res.status_code == 429:
            if cached is not None:
                return cached
            raise limited()
        if res.status_code >= 400:
            raise UpstreamUnavailable()
        arr = res.json()
        if not isinstance(arr, list) or len(arr) != N * N:
            raise UpstreamUnavailable("网格数据不完整")

        times = arr[0]["hourly"]["time"]
        fields: dict[str, np.ndarray] = {}
        for layer, field in {**FIELD, "wind_direction": "wind_direction_10m"}.items():
            cube = np.full((len(times), N, N), np.nan, dtype=float)
            for idx, item in enumerate(arr):
                i, j = divmod(idx, N)
                vals = item["hourly"].get(field) or []
                cube[: len(vals), i, j] = [v if v is not None else np.nan for v in vals]
            fields[layer] = cube
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("wb") as f:
            np.savez_compressed(f, times=np.array(times), **fields)
        tmp.replace(path)
        for old in path.parent.glob("*.npz"):
            if time.time() - old.stat().st_mtime > 172800:
                old.unlink(missing_ok=True)
        return GridData(block=block, times=times, fields=fields)

    return await _cache.get_or_load(
        f"grid:{current_model.get()}:{block.key}:{datetime.now(UTC):%Y%m%d}", _load
    )


def clear_cache() -> None:
    global _cooldown_until
    _cooldown_until = 0.0
    _cache.clear()
