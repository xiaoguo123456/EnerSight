"""统一气象出网入口：默认直连 Open-Meteo；开启代理池时经代理池请求。

部署见 docs/10「气象出网」。这里是**唯一**处理预算与限流归属的地方：

- 项目总预算在这里统一计一次。调用方不要再各自 `shared.take` —— 分散计费必然漏记
  （云量降级网格就漏了整整一条链路），而代理池的出口预算要和项目预算对得上账。
- 429 的冷却范围也在这里决定。直连只有一个出口，冷却整份共享预算；走代理池时归属已由
  池按出口处理完，**下游不得再设一次全局冷却**，否则一个出口的分钟限流会停掉整池。
- 代理池给不出出口时退回直连（`weather_proxy_direct_fallback`）。直连在池的账本里也是
  一个出口（`DIRECT`），额度、冷却、429 归属全都照记 —— 不记账的兜底就是「悄悄把服务器
  IP 的日额度烧完」，那正是当初要上代理池的原因。

缓存、重试与数据时效仍由调用方负责。
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
    cost = request_cost(kwargs.get("params") or {})
    await shared.take(cost)
    if settings.weather_proxy_pool_enabled:
        from app.providers.weather_proxy_pool import PoolExhausted, pool

        try:
            return await pool.get(url, background=background, **kwargs)
        except PoolExhausted as exc:
            # 只有「池子给不出出口」才兜底。上游已经回答过的 429、坏响应、400 都不算：
            # 换条线路出去救不了这次请求，只会多烧一份额度。
            if not pool.direct_ready(cost):
                raise
            log.warning("气象代理池不可用，本次退回直连：%s", exc.message)
            pool.charge_direct(cost)
            res = await client.get(url, **kwargs)
            if res.status_code == 429:
                pool.limit_direct(res)
                _log_limit(res)
            return res
    res = await client.get(url, **kwargs)
    if res.status_code == 429:
        _cooldown(res)
    return res


def _log_limit(res: httpx.Response) -> tuple[str, float]:
    """认出上游 429 的窗口并落日志。分钟/小时/日差三个数量级，恢复时间不能一刀切。

    上游正文形如 `Daily API request limit exceeded. Please try again tomorrow.`。
    以前一律按 60 秒恢复，日额度耗尽时等于每分钟再去撞一次；原因也从没落过日志，
    事后查不出撞的是哪个桶。
    """
    body = res.text[:500]
    scope = scope_of(body)
    name, default = scope if scope else ("unknown", 60.0)
    seconds = retry_seconds(res.headers.get("Retry-After"), default)
    log.warning("气象上游限流 scope=%s seconds=%.0f reason=%s", name, seconds, body[:200])
    return name, seconds


def _cooldown(res: httpx.Response) -> None:
    """直连撞限流：按上游写明的窗口冷却整份共享预算。"""
    _, seconds = _log_limit(res)
    shared.retry_after(None, seconds)
