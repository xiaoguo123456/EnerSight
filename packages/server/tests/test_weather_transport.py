"""气象出网：默认直连；开启代理池时统一经代理池，不漏出直连。预算与限流归属都在这一层。"""

import time
from collections import deque

import httpx
import pytest
import respx

from app.config import settings
from app.providers import weather_proxy_pool
from app.providers.budget import shared
from app.providers.weather_transport import weather_get


def forecast_url() -> str:
    return f"{settings.open_meteo_base}/forecast"


@pytest.fixture(autouse=True)
def budget(monkeypatch):
    # conftest 默认把预算关掉（0 = 不限），这里要验证计费本身
    monkeypatch.setattr(settings, "upstream_units_per_minute", 480)
    monkeypatch.setattr(shared, "marks", deque())
    monkeypatch.setattr(shared, "paused_until", 0)
    return shared


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
    assert seen == [(forecast_url(), {"background": False, "params": {"latitude": 39.9}})]


async def test_出网入口按坐标数统一计一次预算(monkeypatch, budget):
    """调用方不再各自 take —— 以前云量降级网格整条链路都没计过费。"""
    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", False)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(forecast_url()).mock(return_value=httpx.Response(200, json={}))
            await weather_get(
                http,
                forecast_url(),
                params={"latitude": ",".join("1" * 25), "minutely_15": "a,b,c"},
            )
    assert sum(v for _, v in budget.marks) == 25


@pytest.mark.parametrize(
    ("reason", "least"),
    [
        ("Minutely API request limit exceeded. Please try again in one minute.", 60),
        ("Hourly API request limit exceeded.", 3600),
        ("Daily API request limit exceeded. Please try again tomorrow.", 86400),
    ],
)
async def test_直连限流按窗口冷却(monkeypatch, budget, reason, least):
    """日额度耗尽时按 60 秒恢复，等于每分钟再去撞一次。窗口写在正文里。"""
    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", False)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(forecast_url()).mock(
                return_value=httpx.Response(429, json={"error": True, "reason": reason})
            )
            res = await weather_get(http, forecast_url(), params={"latitude": 39.9})
    assert res.status_code == 429
    assert budget.paused_until - time.monotonic() >= least - 1


async def test_来源不明的限流仍按保守冷却(monkeypatch, budget):
    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", False)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(forecast_url()).mock(return_value=httpx.Response(429, text="slow down"))
            await weather_get(http, forecast_url())
    assert 59 <= budget.paused_until - time.monotonic() <= 61
