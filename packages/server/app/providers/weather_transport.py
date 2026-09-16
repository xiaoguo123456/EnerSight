"""统一气象出网入口：默认直连 Open-Meteo；开启代理池时经代理池请求。

部署见 docs/10「气象出网」。这里是**唯一**处理预算与限流归属的地方：

- 项目总预算在这里统一计一次。调用方不要再各自 `shared.take` —— 分散计费必然漏记
  （云量降级网格就漏了整整一条链路），而代理池的出口预算要和项目预算对得上账。
- 429 的冷却范围也在这里决定。直连只有一个出口，冷却整份共享预算；走代理池时归属已由
  池按出口处理完，**下游不得再设一次全局冷却**，否则一个出口的分钟限流会停掉整池。

缓存、重试与数据时效仍由调用方负责；代理池失败不自动退回直连。
"""

import logging

import httpx

from app.config import settings
from app.providers.budget import request_cost, retry_seconds, scope_of, shared

log = logging.getLogger(__name__)


async def weather_get(
    client: httpx.AsyncClient, url: str, *, background: bool = False, **kwargs
) -> httpx.Response:
    """background=True 用于后台批量任务：代理池会给它单独的并发名额，优先保页面请求。"""
    await shared.take(request_cost(kwargs.get("params") or {}))
    if settings.weather_proxy_pool_enabled:
        from app.providers.weather_proxy_pool import pool

        return await pool.get(url, background=background, **kwargs)
    res = await client.get(url, **kwargs)
    if res.status_code == 429:
        _cooldown(res)
    return res


def _cooldown(res: httpx.Response) -> None:
    """直连撞限流：按上游写明的窗口冷却，并把原因落日志。

    上游正文形如 `Daily API request limit exceeded. Please try again tomorrow.`，
    分钟/小时/日三种窗口差了三个数量级。以前一律按 60 秒恢复，日额度耗尽时等于
    每分钟再去撞一次；原因也从没落过日志，事后查不出撞的是哪个桶。
    """
    body = res.text[:500]
    scope = scope_of(body)
    name, default = scope if scope else ("unknown", 60.0)
    seconds = retry_seconds(res.headers.get("Retry-After"), default)
    shared.retry_after(None, seconds)
    log.warning("气象上游限流 scope=%s seconds=%.0f reason=%s", name, seconds, body[:200])
