"""统一气象出网入口：默认直连 Open-Meteo；测试环境开启代理池试验时经代理池请求。

部署见 docs/10「气象出网」。请求预算、缓存与重试仍由调用方负责；代理池失败不自动退回直连。
"""

import httpx

from app.config import settings


async def weather_get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    if settings.weather_proxy_pool_enabled:
        from app.providers.weather_proxy_pool import pool

        return await pool.get(url, **kwargs)
    return await client.get(url, **kwargs)
