"""代理池：出口分组计额、限流按窗口归属、失败隔离、持久化与出网边界。

对应 docs/2026-09-15-weather-proxy-pool-strategy.md §七「必须覆盖的自动化场景」。
"""

import asyncio
import time
from collections import deque

import httpx
import pytest

from app.config import settings
from app.errors import UpstreamRateLimited, UpstreamUnavailable
from app.providers import weather_proxy_pool as module
from app.providers.budget import shared
from app.providers.open_meteo import OpenMeteoProvider
from app.providers.weather_proxy_pool import Exit, Node, ProxyPool, public_proxy
from app.providers.weather_transport import weather_get

URL = "https://api.open-meteo.com/v1/forecast"
PROXIES = ["http://8.8.8.8:8080", "http://1.1.1.1:8080", "http://9.9.9.9:8080"]
# 取 docs/10 实测记录里的真实出口地址；文档保留段（203.0.113.x）不是公网 IP，会被过滤
EXITS = ["103.237.102.191", "134.185.103.14", "153.80.240.2"]


def ready(proxy: str, exit_ip: str, latency: float = 1.0) -> Node:
    now = time.time()
    return Node(
        proxy,
        exit_ip=exit_ip,
        exit_seen=exit_ip,
        exit_checked_at=now,
        last_ok=now,
        latency=latency,
        successes=1,
    )


@pytest.fixture
def pool(tmp_path, monkeypatch):
    value = ProxyPool(tmp_path / "state.json")
    for i, (proxy, exit_ip) in enumerate(zip(PROXIES, EXITS, strict=True)):
        value.nodes[proxy] = ready(proxy, exit_ip, latency=i + 1)
        value.exits[exit_ip] = Exit(exit_ip)
    monkeypatch.setattr(module, "pool", value)
    monkeypatch.setattr(settings, "upstream_units_per_minute", 480)
    monkeypatch.setattr(shared, "marks", deque())
    monkeypatch.setattr(shared, "paused_until", 0)
    monkeypatch.setattr(settings, "weather_proxy_pool_enabled", True)
    # 这个文件只测池子本身；退回直连另有 test_weather_transport 覆盖
    monkeypatch.setattr(settings, "weather_proxy_direct_fallback", False)
    return value


def mock_clients(monkeypatch, handler):
    """替换代理池自己建的客户端；出口探测与业务请求都走这里。"""
    original = httpx.AsyncClient
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return original(
            transport=httpx.MockTransport(lambda req: handler(kwargs.get("proxy"), req))
        )

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    return calls


def weather(_proxy, _request):
    return httpx.Response(200, json={"minutely_15": {"time": ["t"]}})


def limited(reason: str, **headers):
    def handler(_proxy, _request):
        return httpx.Response(429, json={"error": True, "reason": reason}, headers=headers)

    return handler


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


# ---- 出口识别与分组 ----


async def test_出口未确认的节点不承接业务(pool, monkeypatch):
    for node in pool.nodes.values():
        node.exit_ip = ""
    calls = mock_clients(monkeypatch, lambda *_: pytest.fail("不应请求网络"))
    with pytest.raises(UpstreamUnavailable):
        await pool.get(URL)
    assert not calls


async def test_首次入池同一轮里连两次确认出口(pool, monkeypatch):
    """两次独立连接，但在同一轮完成 —— 等下一轮确认会让冷启动空窗一个刷新周期。"""
    node = Node("http://8.8.4.4:8080")
    pool.nodes[node.proxy] = node
    calls = mock_clients(monkeypatch, lambda *_: httpx.Response(200, text=EXITS[0]))
    assert await pool._observe_exit(node)
    assert node.exit_ip == EXITS[0]
    assert len(calls) == 2  # 两条独立连接


async def test_出口两次不一致不入池(pool, monkeypatch):
    node = Node("http://8.8.4.4:8080")
    pool.nodes[node.proxy] = node
    seen = iter([EXITS[0], EXITS[1]])
    mock_clients(monkeypatch, lambda *_: httpx.Response(200, text=next(seen)))
    assert not await pool._observe_exit(node)
    assert node.exit_ip == ""


def test_旧版状态的地址留作候选(pool):
    import json

    pool.state.write_text(
        json.dumps(
            {"entries": [{"proxy": PROXIES[0], "successes": 9}, {"proxy": "http://10.0.0.1:1"}]}
        )
    )
    other = ProxyPool(pool.state)
    other.load()
    assert list(other.nodes) == [PROXIES[0]]  # 内网地址过滤掉
    assert other.nodes[PROXIES[0]].successes == 0  # 统计不继承
    assert not other.nodes[PROXIES[0]].usable(time.time())  # 出口仍要重新实测


async def test_出口变化时保留旧出口账本(pool, monkeypatch):
    node = pool.nodes[PROXIES[0]]
    pool.exits[EXITS[0]].charge(30, time.time())
    node.exit_checked_at = 0  # 观测过期，强制复核
    mock_clients(monkeypatch, lambda *_: httpx.Response(200, text="45.67.89.10"))
    assert await pool._observe_exit(node)
    assert node.exit_ip == "45.67.89.10"
    assert pool.exits[EXITS[0]].minute.used(time.time()) == 30  # 旧账不清零


async def test_同出口多节点共享额度(pool, monkeypatch):
    """同一出口换个端口不会多一份额度 —— 代理地址不等于出口地址。"""
    monkeypatch.setattr(settings, "weather_proxy_exit_units_per_minute", 10)
    pool.nodes = {p: ready(p, EXITS[0]) for p in PROXIES[:2]}
    pool.exits = {EXITS[0]: Exit(EXITS[0])}
    calls = mock_clients(monkeypatch, weather)
    await pool.get(URL, params={"latitude": ",".join("1" * 9)})
    with pytest.raises(UpstreamUnavailable):  # 第二个节点没有自己的额度
        await pool.get(URL, params={"latitude": ",".join("1" * 9)})
    assert len(calls) == 1


async def test_额度用尽前完成预留(pool, monkeypatch):
    monkeypatch.setattr(settings, "weather_proxy_exit_units_per_minute", 10)
    mock_clients(monkeypatch, weather)
    for _ in range(3):  # 每次 3 个坐标，三个出口各记一次
        await pool.get(URL, params={"latitude": "1,2,3"})
    assert [round(e.minute.used(time.time())) for e in pool.exits.values()] == [3, 3, 3]


async def test_单批超过出口分钟额度直接报错(pool, monkeypatch):
    monkeypatch.setattr(settings, "weather_proxy_exit_units_per_minute", 10)
    calls = mock_clients(monkeypatch, weather)
    with pytest.raises(UpstreamUnavailable, match="拆批"):
        await pool.get(URL, params={"latitude": ",".join("1" * 40)})
    assert not calls


# ---- 限流归属 ----


async def test_分钟限流只停该出口(pool, monkeypatch):
    """实测：A 出口 429 的同一分钟，B 出口的请求仍然成功。"""
    calls = mock_clients(
        monkeypatch,
        lambda proxy, req: (
            limited("Minutely API request limit exceeded.")(proxy, req)
            if proxy == PROXIES[0]
            else weather(proxy, req)
        ),
    )
    assert (await pool.get(URL)).status_code == 200  # 换到有余额的出口完成
    assert pool.exits[EXITS[0]].cooldown > time.time()
    assert shared.paused_until == 0  # 不误伤整池
    assert len(calls) == 2
    assert calls[1]["proxy"] != PROXIES[0]
    assert (await pool.get(URL)).status_code == 200  # 后续请求绕开冷却中的出口
    assert calls[2]["proxy"] != PROXIES[0]


@pytest.mark.parametrize(
    ("reason", "least"),
    [("Hourly API request limit exceeded.", 3600), ("Daily API request limit exceeded.", 86400)],
)
async def test_小时与日限流按窗口冷却(pool, monkeypatch, reason, least):
    mock_clients(monkeypatch, limited(reason))
    await pool.get(URL)
    assert pool.exits[EXITS[0]].cooldown - time.time() >= least - 1


async def test_来源不明的限流仍停整池(pool, monkeypatch):
    mock_clients(monkeypatch, lambda *_: httpx.Response(429, text="slow down"))
    assert (await pool.get(URL)).status_code == 429
    assert pool.paused_until > time.time()
    with pytest.raises(UpstreamRateLimited):
        await pool.get(URL)
    # 跨重启保持冷却，不因重启放出积压请求
    other = ProxyPool(pool.state)
    other.load()
    monkeypatch.setattr(shared, "paused_until", 0)
    assert other.paused_until > time.time()
    with pytest.raises(UpstreamRateLimited):
        await other.get(URL)


async def test_分钟限流最多换一个出口再试(pool, monkeypatch):
    """换出口只允许一次；不能靠一路换 IP 把上限试出来。"""
    calls = mock_clients(monkeypatch, limited("Minutely API request limit exceeded."))
    assert (await pool.get(URL)).status_code == 429
    assert len(calls) == settings.weather_proxy_attempts == 2
    assert calls[0]["proxy"] != calls[1]["proxy"]


@pytest.mark.parametrize(
    "reason", ["Hourly API request limit exceeded.", "Daily API request limit exceeded."]
)
async def test_小时与日限流不换出口(pool, monkeypatch, reason):
    """换出口救不了当前这次请求，只会多烧一份额度。"""
    calls = mock_clients(monkeypatch, limited(reason))
    assert (await pool.get(URL)).status_code == 429
    assert len(calls) == 1


# ---- 失败与降级 ----


async def test_网络失败换一个出口且额外尝试计预算(pool, monkeypatch):
    def handler(proxy, request):
        if proxy == PROXIES[0]:
            raise httpx.ConnectError("测试连接失败", request=request)
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return weather(proxy, request)

    calls = mock_clients(monkeypatch, handler)
    res = await pool.get(
        URL, params={"latitude": "1,2"}, headers={"Authorization": "secret", "Cookie": "secret"}
    )
    assert res.status_code == 200
    assert len(calls) == 2
    assert calls[0]["proxy"] != calls[1]["proxy"]
    assert sum(v for _, v in shared.marks) == 2  # 第二次尝试也计项目预算
    assert pool.nodes[PROXIES[0]].cooldown > time.time()
    assert all(c["trust_env"] is False and c["verify"] is True for c in calls)
    assert all(c["follow_redirects"] is False for c in calls)


async def test_失败不与Provider重试相乘(pool, monkeypatch):
    def handler(proxy, request):
        raise httpx.ConnectError("测试连接失败", request=request)

    calls = mock_clients(monkeypatch, handler)
    with pytest.raises(UpstreamUnavailable):
        await OpenMeteoProvider(None).forecast(39.9, 116.4)
    assert len(calls) == 2


async def test_空池立即失败且不直连(pool, monkeypatch):
    pool.nodes.clear()
    calls = mock_clients(monkeypatch, lambda *_: pytest.fail("不应请求网络"))
    with pytest.raises(UpstreamUnavailable) as exc:
        await pool.get(URL)
    assert not calls
    # 没有 SSH 时这条消息是唯一能看到池子状态的地方；只给计数，不暴露代理地址
    assert "候选 0" in exc.value.message and "列表刷新尚未进行" in exc.value.message
    assert not any(p.split("//")[1] in exc.value.message for p in PROXIES)


async def test_全部出口冷却时降级(pool, monkeypatch):
    for item in pool.exits.values():
        item.pause(600, "测试", time.time())
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
    assert all(n.failures == 0 for n in pool.nodes.values())


async def test_统一入口拒绝非气象目标(pool):
    with pytest.raises(UpstreamUnavailable):
        await weather_get(None, "https://example.com/secret")


async def test_调用方的超时只能收紧(pool, monkeypatch):
    monkeypatch.setattr(settings, "weather_proxy_request_timeout", 6)
    calls = mock_clients(monkeypatch, weather)
    await pool.get(URL, timeout=40)
    assert calls[0]["timeout"] == 6


async def test_后台任务不占满前台名额(pool, monkeypatch):
    """后台批量任务单独限名额，页面请求不跟它抢。"""
    monkeypatch.setattr(settings, "weather_proxy_background_slots", 1)
    monkeypatch.setattr(settings, "weather_proxy_slots", 4)
    hold, release = asyncio.Event(), asyncio.Event()

    async def slow(proxy, request):
        if proxy == PROXIES[0]:
            hold.set()
            await release.wait()
        return weather(proxy, request)

    original = httpx.AsyncClient

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(lambda req: slow(kwargs.get("proxy"), req)))

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    first = asyncio.create_task(pool.get(URL, background=True))
    await asyncio.wait_for(hold.wait(), 1)
    second = asyncio.create_task(pool.get(URL, background=True))
    await asyncio.sleep(0)
    assert not second.done()  # 后台名额已满，排队
    assert (await pool.get(URL)).status_code == 200  # 前台照常
    release.set()
    assert (await first).status_code == 200
    assert (await second).status_code == 200


# ---- 持久化与恢复 ----


def test_重启恢复用量与冷却(pool):
    now = time.time()
    pool.exits[EXITS[0]].charge(120, now)
    pool.exits[EXITS[1]].pause(900, "daily 429", now)
    pool.save()
    other = ProxyPool(pool.state)
    other.load()
    assert round(other.exits[EXITS[0]].day.used(now)) == 120
    assert other.exits[EXITS[1]].cooldown > now + 800
    # 最后一次落盘之后的用量没记上，先过完一个分钟窗口再用
    assert other.exits[EXITS[0]].cooldown > now


def test_账本损坏的出口先暂停而不是清零(pool, monkeypatch):
    import json

    monkeypatch.setattr(settings, "weather_proxy_ledger_recover_seconds", 1800)
    pool.save()
    raw = json.loads(pool.state.read_text())
    raw["exits"][0]["day"] = "坏了"
    pool.state.write_text(json.dumps(raw))
    other = ProxyPool(pool.state)
    other.load()
    assert other.exits[EXITS[0]].cooldown > time.time() + 1700


def test_状态恢复保留统计但不复用过期节点(pool):
    pool.nodes[PROXIES[0]].last_ok = time.time() - settings.weather_proxy_health_ttl - 10
    pool.nodes[PROXIES[1]].successes = 7
    pool.save()
    other = ProxyPool(pool.state)
    other.load()
    now = time.time()
    assert not other.nodes[PROXIES[0]].usable(now)
    assert other.nodes[PROXIES[1]].successes == 7


def test_启动候选必须经过检测(pool):
    import json

    pool.nodes.clear()
    pool.state.with_name("weather-proxy-seeds.json").write_text(json.dumps(PROXIES))
    other = ProxyPool(pool.state)
    other.load()
    assert len(other.nodes) == 3
    assert not any(n.usable(time.time()) for n in other.nodes.values())


# ---- 后台维护 ----


@pytest.mark.parametrize(
    ("body", "want"),
    [
        ('{"proxies": [{"proxy": "http://8.8.8.8:8080", "ssl": true}]}', ["http://8.8.8.8:8080"]),
        (
            '{"data": [{"ip": "8.8.8.8", "port": "8080", "protocols": ["http"]}]}',
            ["http://8.8.8.8:8080"],
        ),
        ("8.8.8.8:8080\n\n1.1.1.1:3128\n", ["http://8.8.8.8:8080", "http://1.1.1.1:3128"]),
        ('["8.8.8.8:8080"]', ["http://8.8.8.8:8080"]),
        ("10.0.0.1:8080\n127.0.0.1:1", []),  # 内网一律过滤
    ],
)
def test_列表源三种格式都能解析(body, want):
    """换源只改配置 —— 默认源在境内拉不通就是这么发现的。"""
    assert sorted(module.candidate_proxies(body)) == sorted(want)


@pytest.mark.parametrize(
    ("body", "want"),
    [
        ("103.237.102.191\n", "103.237.102.191"),
        ('{"ip": "103.237.102.191"}', "103.237.102.191"),
        ("fl=471f344\nh=www.cloudflare.com\nip=103.237.102.191\n", "103.237.102.191"),
        ("<html>nope</html>", None),
        ("10.0.0.1", None),
    ],
)
def test_出口探测三种返回都能解析(body, want):
    assert module.parse_exit(body) == want


async def test_后台列表过滤与探测计预算(pool, monkeypatch):
    pool.nodes.clear()
    pool.exits.clear()

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
        if str(req.url).startswith(module.EXIT_PROBE):
            return httpx.Response(200, text=EXITS[0])
        assert str(req.url) == module.PROBE
        return httpx.Response(200, json={"last_run_initialisation_time": 12345})

    mock_clients(monkeypatch, handler)
    await pool.refresh()
    assert list(pool.nodes) == [PROXIES[0]]
    assert pool.nodes[PROXIES[0]].exit_ip == EXITS[0]
    assert pool.ready_exits(time.time()) == 1
    assert sum(v for _, v in shared.marks) == 1  # 元数据探测计一次，出口探测不计


async def test_列表直连失败通过已有代理更新(pool, monkeypatch):
    def handler(proxy, req):
        if proxy is None:
            raise httpx.ConnectError("列表直连不可达", request=req)
        return httpx.Response(200, json={"proxies": []})

    calls = mock_clients(monkeypatch, handler)
    assert await pool.refresh()
    assert calls[1]["proxy"] in PROXIES
    assert len(pool.nodes) == 3


async def test_列表全部失败不淘汰已有节点(pool, monkeypatch):
    def handler(proxy, req):
        if str(req.url).startswith(module.SOURCE):
            raise httpx.ConnectError("列表不可达", request=req)
        return httpx.Response(200, json={"last_run_initialisation_time": 12345})

    mock_clients(monkeypatch, handler)
    assert not await pool.refresh()
    assert len(pool.nodes) == 3
    assert pool.ready_exits(time.time()) == 3


async def test_出口探测失败的候选会被冷却并淘汰(pool, monkeypatch):
    """死候选不记失败就会永远占着探测名额 —— 候选提到几百个时这是致命的。"""
    node = Node("http://8.8.4.4:8080")
    pool.nodes[node.proxy] = node
    mock_clients(monkeypatch, lambda *_: httpx.Response(500))
    await pool._probe(node)
    assert node.failures == 1
    assert node.streak == 1
    assert node.cooldown > time.time()
    assert node not in [
        n for n in pool.nodes.values() if not n.usable(time.time()) and n.cooldown <= time.time()
    ]


async def test_低水位只探候选不重抓列表(pool, monkeypatch):
    """候选还没探完就再抓一遍列表，等于拿新候选挤掉还没验过的老候选。"""
    for n in pool.nodes.values():
        n.exit_checked_at = 0  # 全部待复核
    hits = {"list": 0}

    def handler(proxy, req):
        if str(req.url).startswith(module.SOURCE.split("?")[0]):
            hits["list"] += 1
            return httpx.Response(200, json={"data": []})
        if str(req.url).startswith(module.EXIT_PROBE):
            return httpx.Response(200, text=EXITS[0])
        return httpx.Response(200, json={"last_run_initialisation_time": 1})

    mock_clients(monkeypatch, handler)
    assert pool._unprobed() == 3
    await pool._probe_round()
    assert hits["list"] == 0  # 只探，没抓列表


async def test_候选上限可配置(pool, monkeypatch):
    monkeypatch.setattr(settings, "weather_proxy_candidates", 4)
    rows = [{"proxy": f"http://8.8.8.{i}:8080", "ssl": True} for i in range(1, 20)]

    def handler(proxy, req):
        if str(req.url).startswith(module.SOURCE):
            return httpx.Response(200, json={"proxies": rows})
        raise httpx.ConnectError("不测", request=req)

    mock_clients(monkeypatch, handler)
    await pool.refresh()
    assert len(pool.nodes) == 4
