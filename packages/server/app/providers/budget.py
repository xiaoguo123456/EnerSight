"""同进程所有 Open-Meteo 请求共享滑动窗口预算，缓存命中不计费。

限流归属也在这一层定义：上游 429 的正文写明了窗口（分钟 / 小时 / 日），
差了三个数量级，冷却时长必须跟着窗口走，不能一律 60 秒再去撞一次。
"""

import asyncio
import time
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.config import settings
from app.errors import UpstreamRateLimited

# Open-Meteo 的 429 正文，例如
# `Minutely API request limit exceeded. Please try again in one minute.`
SCOPES = (("minutely", "minute", 60), ("hourly", "hour", 3600), ("daily", "day", 86400))


def scope_of(body: str) -> tuple[str, int] | None:
    """从 429 正文认出限流窗口；认不出返回 None，由调用方按最保守的方式处理。"""
    lowered = body.lower()
    for word, name, seconds in SCOPES:
        if word in lowered:
            return name, seconds
    return None


def retry_seconds(value: str | None, default: float = 60.0) -> float:
    """Retry-After 支持秒数与 HTTP 日期；缺省按窗口给保守值。"""
    if not value:
        return default
    try:
        return max(1.0, float(value))
    except ValueError:
        with suppress(ValueError, TypeError):
            return max(1.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
    return default


class RequestBudget:
    def __init__(self):
        self.marks = deque()
        self.lock = asyncio.Lock()
        self.paused_until = 0.0

    async def take(self, cost: float):
        limit = settings.upstream_units_per_minute
        if limit <= 0:
            return
        remaining = cost
        while remaining > 0:
            portion = min(remaining, limit)
            async with self.lock:
                now = time.monotonic()
                while self.marks and self.marks[0][0] <= now - 60:
                    self.marks.popleft()
                # 上游冷却不是可等待的本地速率预算：前台请求须立即返回配额错误。
                if self.paused_until > now:
                    raise UpstreamRateLimited()
                delay = 0
                if sum(v for _, v in self.marks) + portion > limit:
                    delay = max(delay, self.marks[0][0] + 60 - now)
                if delay <= 0:
                    self.marks.append((now, portion))
                    remaining -= portion
                    continue
            await asyncio.sleep(delay)

    def retry_after(self, value: str | None, default: float = 60.0):
        self.paused_until = max(
            self.paused_until, time.monotonic() + max(1, retry_seconds(value, default))
        )


shared = RequestBudget()


def request_cost(params: dict) -> float:
    """按坐标、变量和天数保守预留；重试也计入，元数据计一次。"""
    coordinates = len(str(params.get("latitude", "0")).split(","))
    fields = sum(len(str(params[k]).split(",")) for k in ("hourly", "minutely_15") if params.get(k))
    days = int(params.get("forecast_days", 7)) + int(params.get("past_days", 0))
    return coordinates * max(1, fields / 10) * max(1, days / 14)
