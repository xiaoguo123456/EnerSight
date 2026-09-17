"""统一气象出网入口：默认直连 Open-Meteo；开启代理池时经代理池请求。

部署见 docs/10「气象出网」。这里是**唯一**处理预算与限流归属的地方：

- 项目总预算在这里统一计一次。调用方不要再各自 `shared.take` —— 分散计费必然漏记
  （云量降级网格就漏了整整一条链路），而代理池的出口预算要和项目预算对得上账。
- 429 的冷却范围也在这里决定。直连只有一个出口，冷却整份共享预算；走代理池时归属已由
  池按出口处理完，**下游不得再设一次全局冷却**，否则一个出口的分钟限流会停掉整池。
- 代理池给不出出口时退回直连（`weather_proxy_direct_fallback`）。直连在池的账本里也是
  一个出口（`DIRECT`），额度、冷却、429 归属全都照记 —— 不记账的兜底就是「悄悄把服务器
  IP 的日额度烧完」，那正是当初要上代理池的原因。

配了自建实例（`open_meteo_fallback_base` 非空）时多一层主备：

- **主源是我们自己的实例，永远直连**。绝不经代理池 —— 池子是用来分摊官方额度的，
  把自建请求塞进随机公网代理只会又慢又不可靠，而且自建本来就没有额度问题。
- 主源传输错误或 5xx 才退回官方；4xx 不退回（400 是坐标越界、429 是配额，
  换个地址是同样结果）。**兜底那一跳也直连**，不经代理池：自建与代理池是互替的两条
  策略，叠起来只会让一次故障同时动用两套限流账本，排查时说不清是谁在限。
  兜底撞 429 仍按窗口冷却共享预算。
- 主源连续失败到阈值就熔断一段时间，期间直接走兜底，不让每个请求先白等一次超时。
  熔断到期后放一个请求去探主源，成功即恢复；`probe` 会绕过熔断定时去探。
- **兜底要花官方额度**，所以默认只给页面请求用：`background=True` 的后台批量路径
  （全目录、地图网格、模型复核）不退回，宁可让这一轮沿用旧快照 —— 它们一轮几千个
  坐标额度，退回官方照样超额，还会把页面那点额度一起吃掉。

缓存、重试与数据时效仍由调用方负责。
"""

import logging
import time
from datetime import UTC, datetime

import httpx

from app.config import settings
from app.providers.budget import request_cost, retry_seconds, scope_of, shared

log = logging.getLogger(__name__)

# 主源熔断状态，进程内。多实例部署时各自独立，与现有扫描锁、缓存的口径一致（docs/05）
_failures = 0
_open_until = 0.0
_unhealthy = False
_counters = {"primary": 0, "fallback": 0, "trips": 0}
_last: dict[str, str | None] = {"error": None, "failure_at": None, "success_at": None}


def status() -> dict[str, object]:
    """给定时探测与调试用。只有地址与计数，不含请求参数。

    `primary_requests` 覆盖所有打到自建主源的请求，含 `background=True` 的批量路径 ——
    早先只统计「可退回」那一条，整轮全目录跑完计数还是个位数，看不出实际出网量。
    """
    return {
        "primary_base": settings.open_meteo_base,
        "fallback_base": settings.open_meteo_fallback_base or None,
        "fallback_enabled": bool(settings.open_meteo_fallback_base),
        "healthy": not _unhealthy,
        "breaker_open": _open_until > time.monotonic(),
        "breaker_open_seconds_left": max(0.0, round(_open_until - time.monotonic(), 1)),
        "consecutive_failures": _failures,
        "primary_requests": _counters["primary"],
        "fallback_requests": _counters["fallback"],
        "breaker_trips": _counters["trips"],
        "last_error": _last["error"],
        "last_failure_at": _last["failure_at"],
        "last_success_at": _last["success_at"],
    }


def _record_success() -> None:
    global _failures, _open_until, _unhealthy
    _failures = 0
    _open_until = 0.0
    _last["success_at"] = datetime.now(UTC).isoformat()
    if _unhealthy:
        _unhealthy = False
        log.warning("weather upstream 主源恢复：%s", settings.open_meteo_base)


def _record_failure(reason: str) -> None:
    global _failures, _open_until, _unhealthy
    _failures += 1
    _last["error"] = reason
    _last["failure_at"] = datetime.now(UTC).isoformat()
    if _failures < settings.weather_primary_trip_after:
        log.warning(
            "weather upstream 主源失败 %d/%d：%s",
            _failures,
            settings.weather_primary_trip_after,
            reason,
        )
        return
    _open_until = time.monotonic() + settings.weather_primary_probe_seconds
    _counters["trips"] += 1
    if not _unhealthy:
        _unhealthy = True
        log.error(
            "weather upstream 主源熔断：%s 连续失败 %d 次，%.0f 秒内直接走兜底 %s；最近错误 %s",
            settings.open_meteo_base,
            _failures,
            settings.weather_primary_probe_seconds,
            settings.open_meteo_fallback_base,
            reason,
        )


def fallback_url(url: str) -> str | None:
    """主源 URL → 官方兜底 URL。没配兜底、或这个 URL 不属于主源就返回 None。

    自建时预报与归档共用同一个 `/v1`（自建二进制里 `/v1/archive` 就是 ERA5），
    官方归档却在另一个域名上，所以按路径而不是按 base 区分。
    元数据走数据桶，不属于主源，自然不会被改写。
    """
    if not settings.open_meteo_fallback_base:
        return None
    for base in (settings.open_meteo_archive_base, settings.open_meteo_base):
        if not base or not url.startswith(base):
            continue
        suffix = url[len(base) :]
        target = (
            settings.open_meteo_archive_fallback_base
            if suffix.startswith("/archive")
            else settings.open_meteo_fallback_base
        )
        return target.rstrip("/") + suffix
    return None


async def weather_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    background: bool = False,
    allow_fallback: bool | None = None,
    **kwargs,
) -> httpx.Response:
    """background=True 用于后台批量任务：代理池会给它单独的并发名额，优先保页面请求。

    allow_fallback 不传时等于 `not background` —— 「后台批量不退回官方」是默认规则，
    只在需要例外时显式传。

    URL 属于自建主源时**一定走 `_primary`**，与 allow_fallback 无关：
    自建请求绝不能进代理池（池子是分摊官方额度用的），只有兜底那一跳才是官方通道。
    """
    cost = request_cost(kwargs.get("params") or {})
    await shared.take(cost)
    if allow_fallback is None:
        allow_fallback = not background
    target = fallback_url(url)
    if target is None:
        return await _official(client, url, cost=cost, background=background, **kwargs)
    return await _primary(
        client,
        url,
        target if allow_fallback else None,
        cost=cost,
        background=background,
        **kwargs,
    )


async def _primary(
    client: httpx.AsyncClient,
    url: str,
    target: str | None,
    *,
    cost: float,
    background: bool,
    **kwargs,
) -> httpx.Response:
    """自建主源：一律直连、单独的超时。target 为 None 表示这条路不许退回官方。

    熔断只在有兜底可用时才短路 —— 没有替代品时再怎么失败也得去打主源。
    """
    if target is not None and _open_until > time.monotonic():
        return await _fallback(client, target, cost=cost, background=background, **kwargs)
    _counters["primary"] += 1
    primary_kwargs = {**kwargs}
    primary_kwargs.setdefault("timeout", settings.weather_primary_timeout)
    try:
        res = await client.get(url, **primary_kwargs)
    except httpx.HTTPError as exc:
        _record_failure(f"{type(exc).__name__}: {exc}")
        if target is None:
            raise
    else:
        if res.status_code < 500:
            _record_success()
            # 自建实例理论上没有额度，但真回了 429 也要按窗口冷却 ——
            # 「429 不管从哪来都要冷却」这条不能因为换了主源就破掉
            if res.status_code == 429:
                _cooldown(res)
            return res
        _record_failure(f"HTTP {res.status_code}")
        if target is None:
            return res
    return await _fallback(client, target, cost=cost, background=background, **kwargs)


async def _fallback(
    client: httpx.AsyncClient, url: str, *, cost: float, background: bool, **kwargs
) -> httpx.Response:
    """兜底直连官方，不经代理池 —— 自建与代理池是互替策略，叠起来一次故障会同时
    动用两套限流账本。预算已在 `weather_get` 里计过一次，这里只管 429 归属。"""
    _counters["fallback"] += 1
    res = await client.get(url, **kwargs)
    if res.status_code == 429:
        _cooldown(res)
    return res


async def _official(
    client: httpx.AsyncClient, url: str, *, cost: float, background: bool, **kwargs
) -> httpx.Response:
    """官方通道：按开关经代理池或直连，429 归属与冷却都在这里决定。"""
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


async def probe(client: httpx.AsyncClient) -> bool:
    """定时探主源。熔断期间也要探，所以直接打自建实例、绕过 `weather_get`。

    最小请求：一个坐标、一个字段、一天。自建实例上是缓存命中，几毫秒。
    不计项目预算 —— 打的是我们自己的实例，不花官方额度。
    """
    if not settings.open_meteo_fallback_base:
        return True
    try:
        res = await client.get(
            f"{settings.open_meteo_base}/forecast",
            params={
                "latitude": 39.9,
                "longitude": 116.4,
                "hourly": "temperature_2m",
                "forecast_days": 1,
            },
            timeout=20,
        )
    except httpx.HTTPError as exc:
        _record_failure(f"probe {type(exc).__name__}: {exc}")
        return False
    if res.status_code >= 500:
        _record_failure(f"probe HTTP {res.status_code}")
        return False
    _record_success()
    return True


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
