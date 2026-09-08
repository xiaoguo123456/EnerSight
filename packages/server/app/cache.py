"""进程内 TTL 缓存。

单实例够用，接口抽象好；水平扩容时换 Redis，调用方不动。docs/05 §五
带 per-key 锁防击穿：同一网格并发请求只回源一次。
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from cachetools import TTLCache


class AsyncTTLCache:
    def __init__(self, maxsize: int, ttl_seconds: int) -> None:
        self._cache: TTLCache[str, Any] = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_load(self, key: str, loader: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit is not None:
                return hit
            value = await loader()
            self._cache[key] = value
            return value

    def clear(self) -> None:
        self._cache.clear()
        self._locks.clear()


def grid_key(latitude: float, longitude: float, step: float = 0.1) -> str:
    """按网格取整做缓存键：相邻站点命中同一份。docs/05 §五"""
    return f"{round(latitude / step) * step:.1f},{round(longitude / step) * step:.1f}"
