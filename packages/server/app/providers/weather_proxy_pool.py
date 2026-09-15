"""测试用免费代理池：后台补充，有限重试，429 共享冷却。部署约定见 docs/10。"""

import asyncio
import ipaddress
import json
import logging
import math
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.errors import UpstreamRateLimited, UpstreamUnavailable
from app.providers.budget import request_cost, shared

log = logging.getLogger(__name__)
SOURCE = "https://api.proxyscrape.com/v4/free-proxy-list/get"
PROBE = "https://api.open-meteo.com/data/dwd_icon/static/meta.json"


def public_proxy(value: str) -> bool:
    """外部列表只允许公网 IP 字面量，拒绝内网、域名、凭据及额外路径。"""
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "http"
            and ipaddress.ip_address(parsed.hostname).is_global
            and 1 <= parsed.port <= 65535
            and parsed.username is None
            and parsed.password is None
            and not (parsed.path or parsed.query or parsed.fragment)
        )
    except (ValueError, TypeError):
        return False


@dataclass
class Entry:
    proxy: str
    successes: int = 0
    failures: int = 0
    streak: int = 0
    latency: float = 6.0
    last_ok: float = 0.0
    cooldown: float = 0.0
    status: int | None = None
    busy: int = 0

    def available(self) -> bool:
        now = time.time()
        return self.cooldown <= now and now - self.last_ok < 1800 and not self.busy


class ProxyPool:
    def __init__(self, state: Path = Path("data/weather-proxies.json")):
        self.state = state
        self.entries: dict[str, Entry] = {}
        self.paused_until = 0.0
        self.task: asyncio.Task | None = None
        self.slots = asyncio.Semaphore(4)

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="weather-proxy-pool")

    async def stop(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
            self.save()

    def load(self):
        try:
            raw = json.loads(self.state.read_text())
            pause = float(raw.get("paused_until", 0))
            if math.isfinite(pause):
                self.paused_until = pause
            for row in raw.get("entries", [])[:40]:
                entry = Entry(**row)
                if public_proxy(entry.proxy) and all(
                    isinstance(v, int | float) and math.isfinite(v) and v >= 0
                    for k, v in asdict(entry).items()
                    if k not in ("proxy", "status")
                ):
                    entry.busy = 0
                    self.entries[entry.proxy] = entry
        except (OSError, ValueError, TypeError, AttributeError):
            log.info("代理池无可恢复状态，将后台重新检测")
        # 首次部署带入官方列表快照，仅作为未验证候选；绝不直接标记可用。
        seed = self.state.with_name("weather-proxy-seeds.json")
        try:
            if time.time() - seed.stat().st_mtime < 86400:
                for proxy in json.loads(seed.read_text())[:40]:
                    if public_proxy(proxy) and len(self.entries) < 40:
                        self.entries.setdefault(proxy, Entry(proxy))
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "saved_at": time.time(),
                        "paused_until": self.paused_until,
                        "entries": [asdict(e) for e in self.entries.values()],
                    }
                )
            )
            tmp.replace(self.state)
        except OSError:
            log.exception("代理池状态保存失败")

    async def _run(self):
        self.load()
        next_refresh = 0.0
        while True:
            try:
                if time.monotonic() >= next_refresh:
                    refreshed = await self.refresh()
                    next_refresh = time.monotonic() + (900 if refreshed else 300)
                self.save()
            except Exception:  # 后台刷新故障不结束维护任务；不打印上游响应。
                log.exception("代理池刷新失败，保留已验证代理")
                next_refresh = time.monotonic() + 60
            await asyncio.sleep(30)

    def _check_pause(self):
        if self.paused_until > time.time() or shared.paused_until > time.monotonic():
            raise UpstreamRateLimited()

    def _record(self, entry: Entry, status: int | None, elapsed: float, ok: bool):
        entry.status = status
        if ok:
            entry.successes += 1
            entry.streak = 0
            entry.last_ok = time.time()
            entry.latency = elapsed if entry.successes == 1 else entry.latency * 0.7 + elapsed * 0.3
        else:
            entry.failures += 1
            entry.streak += 1
            entry.cooldown = time.time() + min(3600, 60 * 2 ** min(entry.streak - 1, 6))
        log.info("气象代理 proxy=%s status=%s seconds=%.2f ok=%s", entry.proxy, status, elapsed, ok)

    async def _request(self, entry: Entry, url: str, *, probe=False, **kwargs):
        start = time.monotonic()
        entry.busy += 1
        try:
            async with asyncio.timeout(4 if probe else 6):
                async with httpx.AsyncClient(
                    proxy=entry.proxy,
                    timeout=4 if probe else 6,
                    trust_env=False,
                    verify=True,
                    follow_redirects=False,
                ) as client:
                    res = await client.get(url, **kwargs)
            if res.status_code == 429:
                shared.retry_after(res.headers.get("Retry-After"))
                self.paused_until = max(
                    self.paused_until, time.time() + shared.paused_until - time.monotonic()
                )
                self._record(entry, 429, time.monotonic() - start, False)
                entry.cooldown = max(entry.cooldown, self.paused_until)
                self.save()
                return res
            valid = False
            if res.status_code == 200:
                with suppress(ValueError):
                    raw = res.json()
                    valid = isinstance(raw, dict | list) and bool(raw)
                    if probe:
                        valid = isinstance(raw, dict) and isinstance(
                            raw.get("last_run_initialisation_time"), int | float
                        )
                    elif isinstance(raw, dict) and raw.get("error"):
                        valid = False
            if res.status_code == 400:
                entry.status = 400
                log.info("气象代理 proxy=%s status=400，不重试", entry.proxy)
                return res
            self._record(entry, res.status_code, time.monotonic() - start, valid)
            if not valid:
                raise UpstreamUnavailable("气象代理未返回有效数据")
            return res
        except (httpx.TransportError, TimeoutError):
            self._record(entry, None, time.monotonic() - start, False)
            raise
        finally:
            entry.busy -= 1

    async def get(self, url: str, **kwargs) -> httpx.Response:
        try:
            async with asyncio.timeout(14):
                return await self._get(url, **kwargs)
        except TimeoutError as exc:
            raise UpstreamUnavailable("气象代理请求超时") from exc

    async def _get(self, url: str, **kwargs) -> httpx.Response:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ("api.open-meteo.com", "archive-api.open-meteo.com")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise UpstreamUnavailable("代理池只允许公开气象 HTTPS 地址")
        # 与应用共享客户端隔离：代理请求不继承令牌、Cookie、认证或环境代理。
        if set(kwargs) - {"params", "headers"}:
            raise UpstreamUnavailable("代理池请求选项不受支持")
        kwargs["headers"] = {"Accept": "application/json"}
        self._check_pause()
        async with self.slots:
            used = set()
            for attempt in range(2):
                self._check_pause()
                choices = [
                    e for e in self.entries.values() if e.available() and e.proxy not in used
                ]
                if not choices:
                    raise UpstreamUnavailable("气象代理池暂无可用连接，请稍后重试")
                if attempt:
                    await shared.take(request_cost(kwargs.get("params", {})))
                    self._check_pause()
                    choices = [e for e in choices if e.available()]
                    if not choices:
                        raise UpstreamUnavailable("气象代理池暂无可用连接，请稍后重试")
                entry = min(
                    choices,
                    key=lambda e: (e.failures / max(1, e.successes + e.failures), e.latency),
                )
                used.add(entry.proxy)
                try:
                    log.info("气象代理请求 path=%s attempt=%s", parsed.path, attempt + 1)
                    return await self._request(entry, url, **kwargs)
                except (httpx.TransportError, TimeoutError):
                    continue
            # 抛业务异常，避免 Provider 再乘以 upstream_retries。
            raise UpstreamUnavailable("气象代理连接失败，请稍后重试")

    async def _fetch_rows(self, proxy=None):
        async with httpx.AsyncClient(  # noqa: SIM117
            proxy=proxy, timeout=6, trust_env=False, verify=True, follow_redirects=False
        ) as client:
            async with client.stream(
                "GET",
                SOURCE,
                params={
                    "request": "display_proxies",
                    "protocol": "http",
                    "format": "json",
                    "proxy_format": "protocolipport",
                    "timeout": 3000,
                    "ssl": "yes",
                    "anonymity": "elite",
                    "limit": 100,
                },
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise ValueError("代理列表过大")
        rows = json.loads(body).get("proxies", [])
        if not isinstance(rows, list):
            raise ValueError("代理列表格式错误")
        return rows

    async def refresh(self):
        # 官方接口境内可能无法直连；通过已有公网代理获取同一 HTTPS 列表。
        routes = [None] + [
            e.proxy
            for e in sorted(self.entries.values(), key=lambda e: e.last_ok, reverse=True)
            if not e.busy and e.cooldown <= time.time()
        ][:2]
        rows = []
        refreshed = False
        for route in routes:
            try:
                async with asyncio.timeout(8):
                    rows = await self._fetch_rows(route)
                refreshed = True
                break
            except (httpx.HTTPError, TimeoutError, ValueError):
                log.info("代理列表获取失败 route=%s", route or "direct")
        now = time.time()
        self.entries = {
            k: e
            for k, e in self.entries.items()
            if e.busy or now - e.last_ok < 86400 or e.cooldown > now or not refreshed
        }
        for row in rows:
            if not isinstance(row, dict):
                continue
            proxy = row.get("proxy", "")
            if len(self.entries) >= 40:
                break
            if row.get("ssl") is True and public_proxy(proxy):
                self.entries.setdefault(proxy, Entry(proxy))
        candidates = [
            e
            for e in sorted(self.entries.values(), key=lambda e: e.last_ok, reverse=True)
            if not e.available() and not e.busy and e.cooldown <= time.time()
        ][:20]
        checked = 0

        async def probe(entry):
            nonlocal checked
            try:
                self._check_pause()
                await shared.take(1)
                self._check_pause()
                checked += 1
                await self._request(entry, PROBE, probe=True)
            except (UpstreamRateLimited, httpx.HTTPError, TimeoutError, UpstreamUnavailable):
                pass

        for offset in range(0, len(candidates), 2):
            if sum(e.available() for e in self.entries.values()) >= 20:
                break
            await asyncio.gather(*(probe(e) for e in candidates[offset : offset + 2]))
        log.info(
            "代理池刷新 candidates=%s available=%s checked=%s",
            len(self.entries),
            sum(e.available() for e in self.entries.values()),
            checked,
        )
        return refreshed


pool = ProxyPool()
