"""接口限流：按 token（登录用户）或客户端 IP，滑动窗口。docs/06 §十三 429 RATE_LIMITED

进程内实现，单实例够用；多实例换 Redis 计数，中间件不动。
不限 /health 与 /docs；限流响应沿用契约的 error 结构并带 Retry-After。
"""

import hashlib
import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings

WINDOW_SECONDS = 60
MAX_KEYS = 20_000
EXEMPT_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc", "/tiles/")

_hits: dict[str, deque[float]] = {}


def client_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and len(auth) > 7:
        return "t:" + hashlib.sha1(auth[7:].encode()).hexdigest()[:16]
    ip = request.client.host if request.client else "unknown"
    if settings.trust_forwarded_for:
        # 只在 ALB/反代前置且安全组已限制直连时开启，否则 XFF 可伪造
        xff = request.headers.get("x-forwarded-for")
        if xff:
            ip = xff.split(",")[0].strip()
    return "ip:" + ip


def check(key: str, limit: int, now: float | None = None) -> int:
    """记录一次访问并返回需等待的秒数；0 表示放行。"""
    now = now if now is not None else time.monotonic()
    q = _hits.get(key)
    if q is None:
        if len(_hits) >= MAX_KEYS:
            _hits.clear()  # 极端情况下整体重置，宁可放行也不要无限增长
        q = _hits[key] = deque()
    cutoff = now - WINDOW_SECONDS
    while q and q[0] <= cutoff:
        q.popleft()
    if len(q) >= limit:
        return max(1, int(q[0] + WINDOW_SECONDS - now) + 1)
    q.append(now)
    return 0


def reset() -> None:
    _hits.clear()


async def middleware(request: Request, call_next):
    limit = settings.rate_limit_per_minute
    if limit <= 0 or request.url.path.startswith(EXEMPT_PREFIXES):
        return await call_next(request)
    wait = check(client_key(request), limit)
    if wait:
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"}},
            headers={"Retry-After": str(wait)},
        )
    return await call_next(request)
