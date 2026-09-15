"""免费代理池的限额、失败隔离、持久化与出网边界。"""

import time
from collections import deque

import httpx
import pytest

from app.config import settings
from app.errors import UpstreamRateLimited, UpstreamUnavailable
from app.providers import weather_proxy_pool as module
from app.providers.budget import shared
from app.providers.open_meteo import OpenMeteoProvider
from app.providers.weather_proxy_pool import Entry, ProxyPool, public_proxy
from app.providers.weather_transport import weather_get

URL = "https://api.open-meteo.com/v1/forecast"
PROXIES = ["http://8.8.8.8:8080", "http://1.1.1.1:8080", "http://9.9.9.9:8080"]


@pytest.fixture
def pool(tmp_path, monkeypatch):
    value = ProxyPool(tmp_path / "state.json")
    value.entries = {p: Entry(p, last_ok=time.time(), latency=i + 1) for i, p in enumerate(PROXIES)}
    monkeypatch.setattr(module, "pool", value)
    monkeypatch.setattr(settings, "upstream_units_per_minute", 480)
    monkeypatch.setattr(shared, "marks", deque())
    monkeypatch.setattr(shared, "paused_until", 0)
    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", True)
    return value


def mock_clients(monkeypatch, handler):
    original = httpx.AsyncClient
    calls = []

    def factory(**kwargs):
        proxy = kwargs.get("proxy")
        calls.append(kwargs)
        return original(transport=httpx.MockTransport(lambda req: handler(proxy, req)))

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    return calls


@pytest.mark.parametrize(
    "address",
    [
        "http://127.0.0.1:80",
        "http://10.0.0.1:80",
        "http://169.254.169.254:80",
        "http://example.com:80",
        "http://user:pass@8.8.8.8:80",
        "http://8.8.8.8:80/path",
        "http://[::1]:80",
        "http://8.8.8.8:99999",
        "https://8.8.8.8:80",
        "http://8.8.8.8",
    ],
)
def test_拒绝非公网代理和异常地址(address):
    assert not public_proxy(address)


async def test_网络失败换一次且额外尝试计预算(pool, monkeypatch):
    def handler(proxy, request):
        if proxy == PROXIES[0]:
            raise httpx.ConnectError("测试连接失败", request=request)
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(200, json={"minutely_15": {"time": ["t"]}})

    calls = mock_clients(monkeypatch, handler)
    res = await pool.get(
        URL, params={"latitude": "1,2"}, headers={"Authorization": "secret", "Cookie": "secret"}
    )
    assert res.status_code == 200
    assert len(calls) == 2
    assert sum(v for _, v in shared.marks) == 2
    assert pool.entries[PROXIES[0]].cooldown > time.time()
    assert all(c["trust_env"] is False and c["verify"] is True for c in calls)
    assert all(c["follow_redirects"] is False for c in calls)


async def test_失败不与Provider重试相乘(pool, monkeypatch):
    def handler(proxy, request):
        raise httpx.ConnectError("测试连接失败", request=request)

    calls = mock_clients(monkeypatch, handler)
    with pytest.raises(UpstreamUnavailable):
        await OpenMeteoProvider(None).forecast(39.9, 116.4)
    assert len(calls) == 2


async def test_429不换代理并跨重启保持冷却(pool, monkeypatch):
    calls = mock_clients(
        monkeypatch, lambda *_: httpx.Response(429, headers={"Retry-After": "120"})
    )
    res = await pool.get(URL)
    assert res.status_code == 429
    with pytest.raises(UpstreamRateLimited):
        await pool.get(URL)
    assert len(calls) == 1
    other = ProxyPool(pool.state)
    other.load()
    monkeypatch.setattr(shared, "paused_until", 0)
    assert other.paused_until > time.time() + 110
    with pytest.raises(UpstreamRateLimited):
        await other.get(URL)


async def test_空池立即失败且不直连(pool, monkeypatch):
    pool.entries.clear()
    calls = mock_clients(monkeypatch, lambda *_: pytest.fail("不应请求网络"))
    with pytest.raises(UpstreamUnavailable):
        await pool.get(URL)
    assert not calls


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>异常</html>"),
        httpx.Response(302, headers={"Location": "http://127.0.0.1"}),
        httpx.Response(503),
    ],
)
async def test_异常响应不换代理也不跟随跳转(pool, monkeypatch, response):
    calls = mock_clients(monkeypatch, lambda *_: response)
    with pytest.raises(UpstreamUnavailable):
        await pool.get(URL)
    assert len(calls) == 1


async def test_400透传不惩罚代理(pool, monkeypatch):
    calls = mock_clients(monkeypatch, lambda *_: httpx.Response(400, json={"error": True}))
    assert (await pool.get(URL)).status_code == 400
    assert len(calls) == 1
    assert pool.entries[PROXIES[0]].failures == 0


async def test_统一入口拒绝非气象目标(pool):
    with pytest.raises(UpstreamUnavailable):
        await weather_get(None, "https://example.com/secret")


def test_状态恢复保留统计但不复用过期代理(pool):
    pool.entries[PROXIES[0]].last_ok = time.time() - 1900
    pool.entries[PROXIES[1]].successes = 7
    pool.entries[PROXIES[1]].busy = 1
    pool.save()
    other = ProxyPool(pool.state)
    other.load()
    assert not other.entries[PROXIES[0]].available()
    assert other.entries[PROXIES[1]].available()
    assert other.entries[PROXIES[1]].successes == 7


async def test_后台列表过滤与探测计预算(pool, monkeypatch):
    pool.entries.clear()

    def handler(proxy, req):
        if proxy is None:
            return httpx.Response(
                200,
                json={
                    "proxies": [
                        {"proxy": "http://127.0.0.1:80", "ssl": True},
                        {"proxy": PROXIES[0], "ssl": True},
                    ]
                },
            )
        assert proxy == PROXIES[0]
        assert str(req.url) == module.PROBE
        return httpx.Response(200, json={"last_run_initialisation_time": 12345})

    calls = mock_clients(monkeypatch, handler)
    await pool.refresh()
    assert len(calls) == 2
    assert list(pool.entries) == [PROXIES[0]]
    assert pool.entries[PROXIES[0]].available()
    assert sum(v for _, v in shared.marks) == 1


async def test_列表直连失败通过已有代理更新(pool, monkeypatch):
    def handler(proxy, req):
        if proxy is None:
            raise httpx.ConnectError("列表直连不可达", request=req)
        return httpx.Response(200, json={"proxies": []})

    calls = mock_clients(monkeypatch, handler)
    assert await pool.refresh()
    assert len(calls) == 2
    assert calls[1]["proxy"] in PROXIES
    assert len(pool.entries) == 3


def test_启动候选必须经过检测(pool):
    import json

    pool.state.with_name("weather-proxy-seeds.json").write_text(json.dumps(PROXIES))
    other = ProxyPool(pool.state)
    other.load()
    assert len(other.entries) == 3
    assert not any(e.available() for e in other.entries.values())


async def test_列表全部失败仍验证已有候选(pool, monkeypatch):
    for e in pool.entries.values():
        e.last_ok = 0

    def handler(proxy, req):
        if str(req.url).startswith(module.SOURCE):
            raise httpx.ConnectError("列表不可达", request=req)
        return httpx.Response(200, json={"last_run_initialisation_time": 12345})

    mock_clients(monkeypatch, handler)
    assert not await pool.refresh()
    assert all(e.available() for e in pool.entries.values())
