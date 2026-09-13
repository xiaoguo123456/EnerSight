"""同进程所有 Open-Meteo 请求共享滑动窗口预算，缓存命中不计费。"""

import asyncio
import time
from collections import deque
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.config import settings
from app.errors import UpstreamRateLimited


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

    def retry_after(self, value: str | None):
        try:
            seconds = float(value) if value else 60.0
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError):
                seconds = 60.0
        self.paused_until = max(self.paused_until, time.monotonic() + max(1, seconds))


shared = RequestBudget()


def request_cost(params: dict) -> float:
    """按坐标、变量和天数保守预留；重试也计入，元数据计一次。"""
    coordinates = len(str(params.get("latitude", "0")).split(","))
    fields = sum(len(str(params[k]).split(",")) for k in ("hourly", "minutely_15") if params.get(k))
    days = int(params.get("forecast_days", 7)) + int(params.get("past_days", 0))
    return coordinates * max(1, fields / 10) * max(1, days / 14)
