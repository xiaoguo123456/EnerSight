"""气象出网：默认直连；开启代理池试验时统一经代理池，不漏出直连。"""

import httpx
import respx

from app.config import settings
from app.providers import weather_proxy_pool
from app.providers.weather_transport import weather_get


def forecast_url() -> str:
    return f"{settings.open_meteo_base}/forecast"


async def test_默认直连并透传参数(monkeypatch):
    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", False)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            route = respx.get(forecast_url()).mock(return_value=httpx.Response(200, json={}))
            res = await weather_get(http, forecast_url(), params={"latitude": 39.9})
    assert res.status_code == 200
    assert route.calls[0].request.url.params["latitude"] == "39.9"


async def test_开启代理池时不直连(monkeypatch):
    seen = []

    async def via_pool(url, **kwargs):
        seen.append((url, kwargs))
        return httpx.Response(200, json={})

    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", True)
    monkeypatch.setattr(weather_proxy_pool.pool, "get", via_pool)
    async with httpx.AsyncClient() as http:
        with respx.mock:  # 没有注册任何路由，直连会直接失败
            res = await weather_get(http, forecast_url(), params={"latitude": 39.9})
            assert not respx.calls
    assert res.status_code == 200
    assert seen == [(forecast_url(), {"params": {"latitude": 39.9}})]
