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


# ---- 代理池不可用时退回直连（docs/10「气象代理池」）----


@pytest.fixture
def empty_pool(monkeypatch, tmp_path):
    from app.providers import weather_proxy_pool as module
    from app.providers.weather_proxy_pool import ProxyPool

    value = ProxyPool(tmp_path / "state.json")
    monkeypatch.setattr(module, "pool", value)
    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", True)
    monkeypatch.setattr(settings, "weather_proxy_direct_fallback", True)
    return value


async def test_池子空了退回直连并记在直连出口账上(monkeypatch, empty_pool, budget):
    from app.providers.weather_proxy_pool import DIRECT

    async with httpx.AsyncClient() as http:
        with respx.mock:
            route = respx.get(forecast_url()).mock(return_value=httpx.Response(200, json={"a": 1}))
            res = await weather_get(http, forecast_url(), params={"latitude": "1,2,3"})
    assert res.status_code == 200
    assert route.called
    assert empty_pool.exits[DIRECT].day.used(time.time()) == 3  # 兜底照样记账


async def test_关掉兜底则池子空了直接失败(monkeypatch, empty_pool, budget):
    from app.errors import UpstreamUnavailable

    monkeypatch.setattr(settings, "weather_proxy_direct_fallback", False)
    async with httpx.AsyncClient() as http:
        with respx.mock:  # 没注册路由，真直连会失败
            with pytest.raises(UpstreamUnavailable):
                await weather_get(http, forecast_url())
            assert not respx.calls


async def test_直连出口额度用完就不再兜底(monkeypatch, empty_pool, budget):
    from app.errors import UpstreamUnavailable

    monkeypatch.setattr(settings, "weather_proxy_direct_units_per_day", 2)
    empty_pool.charge_direct(2)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            with pytest.raises(UpstreamUnavailable):
                await weather_get(http, forecast_url(), params={"latitude": "1"})
            assert not respx.calls


async def test_共享预算冷却期间不兜底(monkeypatch, empty_pool, budget):
    """冷却是刻意设的保护，绕过去就没意义了。预算层先失败，根本走不到池子。"""
    from app.errors import UpstreamRateLimited

    budget.retry_after(None, 120)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            with pytest.raises(UpstreamRateLimited):
                await weather_get(http, forecast_url())
            assert not respx.calls


async def test_兜底撞429按窗口停直连出口(monkeypatch, empty_pool, budget):
    from app.providers.weather_proxy_pool import DIRECT

    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(forecast_url()).mock(
                return_value=httpx.Response(
                    429, json={"error": True, "reason": "Daily API request limit exceeded."}
                )
            )
            res = await weather_get(http, forecast_url(), params={"latitude": "1"})
    assert res.status_code == 429
    assert empty_pool.exits[DIRECT].cooldown - time.time() >= 86000
    # 代理出口没被牵连：这是直连这个身份的额度问题
    assert empty_pool.paused_until == 0


async def test_上游回答过的失败不兜底(monkeypatch, budget, tmp_path):
    """池子里有出口、但上游返回 429，这时换直连只会多烧一份额度。"""
    from app.providers import weather_proxy_pool as module
    from app.providers.weather_proxy_pool import Exit, Node, ProxyPool

    value = ProxyPool(tmp_path / "state.json")
    now = time.time()
    value.nodes["http://8.8.8.8:8080"] = Node(
        "http://8.8.8.8:8080", exit_ip="1.2.3.4", exit_checked_at=now, last_ok=now, successes=1
    )
    value.exits["1.2.3.4"] = Exit("1.2.3.4")
    monkeypatch.setattr(module, "pool", value)
    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", True)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kw: original(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    429, json={"error": True, "reason": "Daily API request limit exceeded."}
                )
            )
        ),
    )
    async with httpx.AsyncClient() as http:
        with respx.mock:
            res = await weather_get(http, forecast_url(), params={"latitude": "1"})
            assert not respx.calls  # 没有退回直连
    assert res.status_code == 429


# ---- 自建实例的主备与熔断（docs/10「气象出网」、docs/2026-09-16-open-meteo-self-host.md）----

SELF = "http://10.9.9.9:8090/v1"
OFFICIAL = "https://api.open-meteo.com/v1"
OFFICIAL_ARCHIVE = "https://archive-api.open-meteo.com/v1"


@pytest.fixture
def selfhosted(monkeypatch):
    """主源指向自建实例、兜底指向官方，并把进程内熔断状态清干净。"""
    from app.providers import weather_transport as transport

    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", False)
    monkeypatch.setattr(settings, "open_meteo_base", SELF)
    monkeypatch.setattr(settings, "open_meteo_archive_base", SELF)
    monkeypatch.setattr(settings, "open_meteo_fallback_base", OFFICIAL)
    monkeypatch.setattr(settings, "open_meteo_archive_fallback_base", OFFICIAL_ARCHIVE)
    monkeypatch.setattr(settings, "weather_primary_trip_after", 3)
    monkeypatch.setattr(settings, "weather_primary_probe_seconds", 120.0)
    monkeypatch.setattr(transport, "_failures", 0)
    monkeypatch.setattr(transport, "_open_until", 0.0)
    monkeypatch.setattr(transport, "_unhealthy", False)
    monkeypatch.setattr(transport, "_counters", {"primary": 0, "fallback": 0, "trips": 0})
    monkeypatch.setattr(transport, "_last", {"error": None, "failure_at": None, "success_at": None})
    return transport


def test_兜底地址按路径区分预报与归档(selfhosted):
    # 自建把 ERA5 挂在同一个 /v1 下，官方归档却是另一个域名
    assert selfhosted.fallback_url(f"{SELF}/forecast") == f"{OFFICIAL}/forecast"
    assert selfhosted.fallback_url(f"{SELF}/archive") == f"{OFFICIAL_ARCHIVE}/archive"


def test_没配兜底时不改写(selfhosted, monkeypatch):
    monkeypatch.setattr(settings, "open_meteo_fallback_base", "")
    assert selfhosted.fallback_url(f"{SELF}/forecast") is None


def test_元数据地址不被改写(selfhosted):
    # 元数据走数据桶，不属于主源
    url = f"{settings.open_meteo_meta_base}/ecmwf_ifs/static/meta.json"
    assert selfhosted.fallback_url(url) is None


async def test_主源传输错误时退回官方(selfhosted):
    async with httpx.AsyncClient() as http:
        with respx.mock:
            primary = respx.get(f"{SELF}/forecast").mock(side_effect=httpx.ConnectError("refused"))
            official = respx.get(f"{OFFICIAL}/forecast").mock(
                return_value=httpx.Response(200, json={"ok": 1})
            )
            res = await weather_get(http, f"{SELF}/forecast", params={"latitude": 39.9})
    assert res.json() == {"ok": 1}
    assert primary.called and official.called
    assert official.calls[0].request.url.params["latitude"] == "39.9"  # 参数原样带过去
    assert selfhosted.status()["consecutive_failures"] == 1


async def test_主源5xx时退回官方(selfhosted):
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(502))
            official = respx.get(f"{OFFICIAL}/forecast").mock(
                return_value=httpx.Response(200, json={"ok": 1})
            )
            res = await weather_get(http, f"{SELF}/forecast")
    assert res.status_code == 200 and official.called
    assert selfhosted.status()["last_error"] == "HTTP 502"


async def test_主源400不退回(selfhosted):
    """400 是坐标越界，换个地址是同样结果，退回只会白花官方额度。"""
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(400))
            official = respx.get(f"{OFFICIAL}/forecast").mock(
                return_value=httpx.Response(200, json={"ok": 1})
            )
            res = await weather_get(http, f"{SELF}/forecast")
    assert res.status_code == 400
    assert not official.called
    assert selfhosted.status()["consecutive_failures"] == 0  # 4xx 算主源正常应答


async def test_主源429不退回但仍按窗口冷却(selfhosted, budget):
    """自建理论上没有额度，真回了 429 也不能把「429 必冷却」这条破掉。"""
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(
                return_value=httpx.Response(
                    429, json={"error": True, "reason": "Daily API request limit exceeded."}
                )
            )
            official = respx.get(f"{OFFICIAL}/forecast").mock(return_value=httpx.Response(200))
            res = await weather_get(http, f"{SELF}/forecast")
    assert res.status_code == 429 and not official.called
    assert budget.paused_until - time.monotonic() >= 86000


async def test_连续失败到阈值后熔断且不再打主源(selfhosted):
    async with httpx.AsyncClient() as http:
        with respx.mock:
            primary = respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(503))
            respx.get(f"{OFFICIAL}/forecast").mock(return_value=httpx.Response(200, json={}))
            for _ in range(settings.weather_primary_trip_after):
                await weather_get(http, f"{SELF}/forecast")
            tripped = primary.call_count
            st = selfhosted.status()
            assert st["breaker_open"] and not st["healthy"] and st["breaker_trips"] == 1
            for _ in range(3):  # 熔断期间主源计数不应再涨
                await weather_get(http, f"{SELF}/forecast")
            assert primary.call_count == tripped
    assert selfhosted.status()["fallback_requests"] == settings.weather_primary_trip_after + 3


async def test_熔断到期后主源成功即恢复(selfhosted, monkeypatch):
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(503))
            respx.get(f"{OFFICIAL}/forecast").mock(return_value=httpx.Response(200, json={}))
            for _ in range(settings.weather_primary_trip_after):
                await weather_get(http, f"{SELF}/forecast")
            assert selfhosted.status()["breaker_open"]
            monkeypatch.setattr(selfhosted, "_open_until", 0.0)  # 冷却到期
            respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(200, json={"ok": 1}))
            res = await weather_get(http, f"{SELF}/forecast")
    assert res.json() == {"ok": 1}
    st = selfhosted.status()
    assert st["healthy"] and st["consecutive_failures"] == 0 and not st["breaker_open"]


@pytest.mark.parametrize("kwargs", [{"background": True}, {"allow_fallback": False}])
async def test_后台批量不退回官方(selfhosted, kwargs):
    """全目录、地图网格、模型复核：一轮几千个坐标额度，退回官方照样超额。"""
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(side_effect=httpx.ConnectError("refused"))
            official = respx.get(f"{OFFICIAL}/forecast").mock(
                return_value=httpx.Response(200, json={})
            )
            with pytest.raises(httpx.ConnectError):
                await weather_get(http, f"{SELF}/forecast", **kwargs)
    assert not official.called


async def test_兜底不经代理池(selfhosted, monkeypatch):
    """自建与代理池是互替的两条策略，叠起来一次故障会同时动用两套限流账本。"""
    called = []

    async def via_pool(url, **kw):  # 不该被调用
        called.append(url)
        return httpx.Response(200, json={})

    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", True)
    monkeypatch.setattr(weather_proxy_pool.pool, "get", via_pool)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(side_effect=httpx.ConnectError("refused"))
            official = respx.get(f"{OFFICIAL}/forecast").mock(
                return_value=httpx.Response(200, json={"ok": 1})
            )
            res = await weather_get(http, f"{SELF}/forecast")
    assert res.json() == {"ok": 1} and official.called
    assert called == []


async def test_主备两跳只计一次预算(selfhosted, budget):
    """主源不花官方额度，兜底才花；预算在入口计一次，不能因为退回而收两遍。"""
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(side_effect=httpx.ConnectError("refused"))
            respx.get(f"{OFFICIAL}/forecast").mock(return_value=httpx.Response(200, json={}))
            await weather_get(http, f"{SELF}/forecast", params={"latitude": ",".join("1" * 25)})
    assert sum(v for _, v in budget.marks) == 25


async def test_探测绕过熔断(selfhosted, monkeypatch):
    """熔断期间业务走兜底，但探测必须还去打主源，否则永远不会恢复。"""
    monkeypatch.setattr(selfhosted, "_open_until", time.monotonic() + 999)
    monkeypatch.setattr(selfhosted, "_unhealthy", True)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            primary = respx.get(f"{SELF}/forecast").mock(
                return_value=httpx.Response(200, json={"hourly": {}})
            )
            assert await selfhosted.probe(http) is True
            assert primary.called
    st = selfhosted.status()
    assert st["healthy"] and not st["breaker_open"]


async def test_探测失败也计入熔断(selfhosted):
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(side_effect=httpx.ConnectError("refused"))
            for _ in range(settings.weather_primary_trip_after):
                assert await selfhosted.probe(http) is False
    assert selfhosted.status()["breaker_open"]


async def test_没配兜底时探测直接通过(selfhosted, monkeypatch):
    monkeypatch.setattr(settings, "open_meteo_fallback_base", "")
    async with httpx.AsyncClient() as http:
        with respx.mock:  # 不注册任何路由：真去请求就会失败
            assert await selfhosted.probe(http) is True
            assert not respx.calls


# ---- 自建主源的超时、计数与「不进代理池」（docs/2026-09-16-open-meteo-self-host.md 十三）----


async def test_主源用单独的更长超时(selfhosted, monkeypatch):
    """自建冷读实测 5–6 秒，共用客户端只有 10 秒，超时就静默转去花官方额度。"""
    monkeypatch.setattr(settings, "weather_primary_timeout", 30.0)
    seen = {}
    async with httpx.AsyncClient(timeout=10.0) as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(200, json={}))
            original = http.get

            async def spy(url, **kwargs):
                seen.update(kwargs)
                return await original(url, **kwargs)

            monkeypatch.setattr(http, "get", spy)
            await weather_get(http, f"{SELF}/forecast")
    assert seen["timeout"] == 30.0


async def test_调用方显式给的超时优先(selfhosted, monkeypatch):
    """全目录 40 秒、地图 25 秒是按各自批量大小定的，不该被主源默认值覆盖。"""
    monkeypatch.setattr(settings, "weather_primary_timeout", 30.0)
    seen = {}
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(200, json={}))
            original = http.get

            async def spy(url, **kwargs):
                seen.update(kwargs)
                return await original(url, **kwargs)

            monkeypatch.setattr(http, "get", spy)
            await weather_get(http, f"{SELF}/forecast", timeout=40, background=True)
    assert seen["timeout"] == 40


async def test_后台批量打自建也不进代理池(selfhosted, monkeypatch):
    """background=True 时不退回官方，但它打的是自建 —— 绝不能被路由进代理池。"""
    called = []

    async def via_pool(url, **kw):
        called.append(url)
        return httpx.Response(200, json={})

    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", True)
    monkeypatch.setattr(weather_proxy_pool.pool, "get", via_pool)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            primary = respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(200, json={}))
            res = await weather_get(http, f"{SELF}/forecast", background=True)
    assert res.status_code == 200 and primary.called
    assert called == []


async def test_后台批量也计入主源请求数(selfhosted):
    """早先只统计可退回那一条，整轮全目录跑完计数还是个位数，看不出实际出网量。"""
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(200, json={}))
            for _ in range(3):
                await weather_get(http, f"{SELF}/forecast", background=True)
    assert selfhosted.status()["primary_requests"] == 3


async def test_没有兜底可用时熔断不短路(selfhosted, monkeypatch):
    """没有替代品时再怎么失败也得去打主源，否则后台永远拿不到数据。"""
    monkeypatch.setattr(selfhosted, "_open_until", time.monotonic() + 999)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            primary = respx.get(f"{SELF}/forecast").mock(return_value=httpx.Response(200, json={}))
            res = await weather_get(http, f"{SELF}/forecast", background=True)
    assert res.status_code == 200 and primary.called
