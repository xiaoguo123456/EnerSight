"""免费代理池：按实际出口分组计额、按故障范围冷却。

策略见 docs/2026-09-15-weather-proxy-pool-strategy.md，部署约定见 docs/10「气象出网」。

三层对象各管一件事，不要合并：

    节点 Node   能不能连通、走哪条连接
    出口 Exit   额度账本与限流状态 —— 同出口的多个节点共享一本账
    项目预算    providers/budget.shared，控制项目总负载

代理地址不等于出口地址：实测 ``202.141.161.52:10808`` 的出口是 ``134.185.103.14``。
按代理地址记额度会把同一个出口的额度算成两份，所以入池必须实测出口并按出口去重。
"""

import asyncio
import ipaddress
import json
import logging
import math
import random
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.errors import UpstreamRateLimited, UpstreamUnavailable
from app.providers.budget import request_cost, retry_seconds, scope_of, shared

log = logging.getLogger(__name__)

# 免费列表与两个探测目标。都可用配置覆盖，便于换源或在测试里指向本地桩。
#
# 选型依据是 2026-09-16 在测试机（北京 ECS）上的实测，见 docs/10「气象代理池」：
# ProxyScrape 直连超时拉不到列表；geonode 0.75 秒返回，`cdn.jsdelivr.net` 的
# TheSpeedX/PROXY-List 纯文本镜像 1.55 秒返回，两种格式都支持，换源只改配置。
SOURCE = "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc&protocols=http"
PROBE = "https://api.open-meteo.com/data/dwd_icon/static/meta.json"
# 出口探测点要「代理能访问到」，不是「我们能访问到」。同样一批代理实测：
# checkip / icanhazip / cloudflare-trace / ifconfig 都是 5/5，api.ipify.org 只有 3/5 ——
# 用 ipify 会把四成本来能干活的代理挡在池外。返回纯文本 IP，解析见 _exit_ip。
EXIT_PROBE = "https://checkip.amazonaws.com"
ALLOWED_HOSTS = ("api.open-meteo.com", "archive-api.open-meteo.com")
# 直连在账本里也是一个出口：服务器自己的 IP。兜底不记账就等于回到最初那个问题 ——
# 悄悄把这个 IP 的日额度烧完，而且看不见是谁烧的。
DIRECT = "direct"


class PoolExhausted(UpstreamUnavailable):
    """池子给不出出口（空池、全冷却、无余额，或几次尝试全是传输错误）。

    只有这一种失败才允许退回直连：上游已经回答过的 429、坏响应、400 都不算，
    换条线路出去只会多烧一份额度。
    """


class ExitLimited(Exception):
    """该出口撞了分钟限流。允许换一个有余额的出口再试一次，不是最终结果。"""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__("exit minute limited")


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


def parse_exit(body: str) -> str | None:
    """出口探测的返回：纯文本 IP、JSON，或 cloudflare trace 那种 key=value 行。"""
    body = body.strip()[:2000]
    direct = public_ip(body)
    if direct:
        return direct
    with suppress(ValueError, TypeError):
        raw = json.loads(body)
        found = public_ip(raw.get("ip") if isinstance(raw, dict) else raw)
        if found:
            return found
    for line in body.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "ip":
            return public_ip(value)
    return None


def candidate_proxies(body: str) -> list[str]:
    """把列表源的返回归一成代理 URL。

    三种格式：ProxyScrape 的 {"proxies":[{"proxy":..}]}、geonode 的
    {"data":[{"ip":..,"port":..}]}、以及纯文本镜像的每行 ip:port。
    换源只改配置，不用改代码 —— 上一次换源就是因为默认源在境内拉不通。
    """

    def one(row: object) -> str | None:
        if isinstance(row, str):
            value = row.strip()
            value = value if "://" in value else f"http://{value}"
            return value if public_proxy(value) else None
        if not isinstance(row, dict):
            return None
        if row.get("proxy"):
            return one(str(row["proxy"]))
        if row.get("ip") and row.get("port"):
            return one(f"{row['ip']}:{row['port']}")
        return None

    rows: list = []
    with suppress(ValueError, TypeError):
        raw = json.loads(body)
        if isinstance(raw, dict):
            rows = raw.get("proxies") or raw.get("data") or []
        elif isinstance(raw, list):
            rows = raw
    if not rows:
        rows = [line for line in body.splitlines() if line.strip()]
    if not isinstance(rows, list):
        raise ValueError("代理列表格式错误")
    return [v for v in (one(r) for r in rows) if v]


def public_ip(value: object) -> str | None:
    """出口探测的返回值：必须是公网 IPv4 字面量，否则当没测到。"""
    try:
        address = ipaddress.ip_address(str(value).strip())
    except (ValueError, TypeError):
        return None
    return str(address) if address.version == 4 and address.is_global else None


@dataclass
class Window:
    """滑动窗口账本。整点一次性释放会让一分钟内打满，所以按时间戳逐条淘汰。"""

    seconds: int
    marks: list[list[float]] = field(default_factory=list)

    def trim(self, now: float) -> None:
        self.marks = [m for m in self.marks if m[0] > now - self.seconds]

    def used(self, now: float) -> float:
        self.trim(now)
        return sum(m[1] for m in self.marks)

    def room(self, cost: float, limit: float, now: float) -> bool:
        return limit <= 0 or self.used(now) + cost <= limit

    def add(self, cost: float, now: float) -> None:
        self.marks.append([now, cost])
        self.trim(now)


@dataclass
class Exit:
    """一个实测出口：额度账本、冷却与并发。同出口的多个节点共用这一份。"""

    ip: str
    minute: Window = field(default_factory=lambda: Window(60))
    hour: Window = field(default_factory=lambda: Window(3600))
    day: Window = field(default_factory=lambda: Window(86400))
    cooldown: float = 0.0
    reason: str = ""
    last_used: float = 0.0
    busy: int = 0

    def windows(self) -> tuple[tuple[Window, float], ...]:
        # 直连出口是服务器自己的 IP，额度已知（免费层 600/分、5,000/小时、10,000/天），
        # 默认留两成余量；免费代理的额度不知道，只能给保守得多的自设阈值。
        if self.ip == DIRECT:
            return (
                (self.minute, settings.weather_proxy_direct_units_per_minute),
                (self.hour, settings.weather_proxy_direct_units_per_hour),
                (self.day, settings.weather_proxy_direct_units_per_day),
            )
        return (
            (self.minute, settings.weather_proxy_exit_units_per_minute),
            (self.hour, settings.weather_proxy_exit_units_per_hour),
            (self.day, settings.weather_proxy_exit_units_per_day),
        )

    def free(self, now: float) -> bool:
        """出口本身可用：不在冷却、没有在途请求（每出口并发 1）。"""
        return self.cooldown <= now and not self.busy

    def room(self, cost: float, now: float) -> bool:
        return all(w.room(cost, limit, now) for w, limit in self.windows())

    def charge(self, cost: float, now: float) -> None:
        """预留与记账是同一步：先扣再发，超时也不退，请求可能已经到达上游。"""
        for w, _ in self.windows():
            w.add(cost, now)
        self.last_used = now

    def pause(self, seconds: float, reason: str, now: float) -> None:
        self.cooldown = max(self.cooldown, now + seconds)
        self.reason = reason


@dataclass
class Node:
    """一条代理连接。exit_ip 是实测出口，未确认前不承接业务。"""

    proxy: str
    exit_ip: str = ""
    exit_seen: str = ""  # 首次观测，待第二次连接确认
    exit_checked_at: float = 0.0
    successes: int = 0
    failures: int = 0
    streak: int = 0
    latency: float = 6.0
    last_ok: float = 0.0
    cooldown: float = 0.0
    status: int | None = None
    busy: int = 0

    def exit_fresh(self, now: float) -> bool:
        return bool(self.exit_ip) and now - self.exit_checked_at < settings.weather_proxy_exit_ttl

    def healthy(self, now: float) -> bool:
        return now - self.last_ok < settings.weather_proxy_health_ttl

    def usable(self, now: float) -> bool:
        return self.cooldown <= now and not self.busy and self.exit_fresh(now) and self.healthy(now)

    def score(self) -> tuple[float, float]:
        return self.failures / max(1, self.successes + self.failures), self.latency


class ProxyPool:
    def __init__(self, state: Path = Path("data/weather-proxies.json")):
        self.state = state
        self.nodes: dict[str, Node] = {}
        self.exits: dict[str, Exit] = {}
        self.paused_until = 0.0  # 来源不明的 429：整池保守冷却
        self.task: asyncio.Task | None = None
        self._slots: asyncio.Semaphore | None = None
        self._background: asyncio.Semaphore | None = None
        self._topped_up = 0.0
        self.last_refresh: tuple[float, bool] = (0.0, False)

    # ---- 生命周期 ----

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

    def slots(self) -> asyncio.Semaphore:
        if self._slots is None:
            self._slots = asyncio.Semaphore(max(1, settings.weather_proxy_slots))
        return self._slots

    def background_slots(self) -> asyncio.Semaphore:
        """后台任务单独限名额，别把出口全占满让页面请求排不上。"""
        if self._background is None:
            self._background = asyncio.Semaphore(max(1, settings.weather_proxy_background_slots))
        return self._background

    # ---- 状态持久化 ----

    def load(self):
        raw: dict = {}
        try:
            raw = json.loads(self.state.read_text())
        except (OSError, ValueError):
            log.info("代理池无可恢复状态，将后台重新检测")
        saved_at = raw.get("saved_at")
        saved_at = float(saved_at) if isinstance(saved_at, int | float) else 0.0
        pause = raw.get("paused_until")
        if isinstance(pause, int | float) and math.isfinite(pause):
            self.paused_until = pause
        for row in raw.get("nodes", [])[: settings.weather_proxy_candidates]:
            node = self._node_from(row)
            if node is not None:
                self.nodes[node.proxy] = node
        for row in raw.get("exits", [])[: settings.weather_proxy_candidates]:
            self._exit_from(row, saved_at)
        # 旧版（v1）状态只有 entries，没有出口信息。地址仍是有用的候选，直接扔掉等于
        # 升级后从零开始等列表源，境内还未必拉得到。统计不继承，出口照样要重新实测。
        for row in raw.get("entries", [])[: settings.weather_proxy_candidates]:
            proxy = row.get("proxy") if isinstance(row, dict) else None
            if public_proxy(proxy) and len(self.nodes) < settings.weather_proxy_candidates:
                self.nodes.setdefault(proxy, Node(proxy))
        # 出口账本缺失时不能当成零用量重新开张 —— 上游的小时与日窗口不会因为我们重启而清零。
        now = time.time()
        for node in self.nodes.values():
            if node.exit_ip and node.exit_ip != DIRECT and node.exit_ip not in self.exits:
                self.exits[node.exit_ip] = Exit(node.exit_ip)
                self.exits[node.exit_ip].pause(
                    settings.weather_proxy_ledger_recover_seconds, "账本缺失，待复核", now
                )
        self._seed()

    def _node_from(self, row: object) -> Node | None:
        if not isinstance(row, dict):
            return None
        try:
            node = Node(**{k: v for k, v in row.items() if k in Node.__dataclass_fields__})
        except TypeError:
            return None
        numbers = (
            node.successes,
            node.failures,
            node.streak,
            node.latency,
            node.last_ok,
            node.cooldown,
            node.exit_checked_at,
        )
        if not public_proxy(node.proxy) or not all(
            isinstance(v, int | float) and math.isfinite(v) and v >= 0 for v in numbers
        ):
            return None
        if node.exit_ip and public_ip(node.exit_ip) is None:
            return None
        node.busy = 0
        return node

    def _exit_from(self, row: object, saved_at: float) -> None:
        if not isinstance(row, dict) or (
            row.get("ip") != DIRECT and public_ip(row.get("ip")) is None
        ):
            return
        item = Exit(str(row["ip"]))
        broken = False
        for name, window in (("minute", item.minute), ("hour", item.hour), ("day", item.day)):
            marks = row.get(name)
            if not isinstance(marks, list):
                broken = True
                continue
            for mark in marks:
                if (
                    isinstance(mark, list)
                    and len(mark) == 2
                    and all(isinstance(v, int | float) and math.isfinite(v) for v in mark)
                ):
                    window.marks.append([float(mark[0]), float(mark[1])])
                else:
                    broken = True
        cooldown = row.get("cooldown")
        if isinstance(cooldown, int | float) and math.isfinite(cooldown):
            item.cooldown = cooldown
        item.reason = str(row.get("reason", ""))[:200]
        now = time.time()
        if broken:
            # 长窗口账本读不回来就不能继续用这个出口，先暂停等复核。
            item.pause(settings.weather_proxy_ledger_recover_seconds, "账本损坏，待复核", now)
        elif saved_at:
            # 最后一次落盘之后的用量没记上，等过完一个分钟窗口再用它。
            item.cooldown = max(item.cooldown, min(saved_at + 60, now + 60))
        self.exits[item.ip] = item

    def _seed(self):
        """首次部署可带入官方列表快照，仅作未验证候选；绝不直接标记可用。"""
        seed = self.state.with_name("weather-proxy-seeds.json")
        try:
            if time.time() - seed.stat().st_mtime > settings.weather_proxy_seed_max_age:
                return
            for proxy in json.loads(seed.read_text()):
                if len(self.nodes) >= settings.weather_proxy_candidates:
                    break
                if public_proxy(proxy):
                    self.nodes.setdefault(proxy, Node(proxy))
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.snapshot()))
            tmp.replace(self.state)
        except OSError:
            log.exception("代理池状态保存失败")

    def snapshot(self) -> dict:
        now = time.time()
        for item in self.exits.values():
            for window, _ in item.windows():
                window.trim(now)
        return {
            "version": 2,
            "saved_at": now,
            "paused_until": self.paused_until,
            "nodes": [asdict(n) for n in self.nodes.values()],
            "exits": [
                {
                    "ip": e.ip,
                    "minute": e.minute.marks,
                    "hour": e.hour.marks,
                    "day": e.day.marks,
                    "cooldown": e.cooldown,
                    "reason": e.reason,
                }
                for e in self.exits.values()
            ],
        }

    def stats(self) -> dict:
        """观测用汇总，docs/10 §七。供排查与后续接口复用。"""
        now = time.time()
        return {
            "nodes": len(self.nodes),
            "ready_nodes": sum(n.usable(now) for n in self.nodes.values()),
            "exits": len(self.exits),
            "ready_exits": self.ready_exits(now),
            "paused_exits": sum(e.cooldown > now for e in self.exits.values()),
            "paused_until": self.paused_until,
            "usage": {
                e.ip: {
                    "minute": round(e.minute.used(now), 1),
                    "hour": round(e.hour.used(now), 1),
                    "day": round(e.day.used(now), 1),
                    "reason": e.reason,
                }
                for e in self.exits.values()
            },
        }

    def ready_exits(self, now: float) -> int:
        """可用独立出口数：有节点能连、出口已验证、不在冷却。这是真正的额度宽度。"""
        ips = {
            n.exit_ip
            for n in self.nodes.values()
            if n.exit_fresh(now) and n.healthy(now) and n.cooldown <= now
        } - {DIRECT}
        return sum(1 for ip in ips if ip in self.exits and self.exits[ip].cooldown <= now)

    # ---- 后台维护 ----

    async def _run(self):
        self.load()
        next_refresh = next_probe = 0.0
        while True:
            try:
                now = time.monotonic()
                low = self.ready_exits(time.time()) < settings.weather_proxy_target_exits
                # 抓列表和探候选是两件事，节奏也该分开。候选还没探完就再抓一遍列表，
                # 等于拿新候选去挤掉还没验过的老候选，出口数反而上不来。
                if now >= next_refresh:
                    refreshed = await self.refresh()
                    # 附少量随机延迟，多实例不要卡在同一秒抓同一个列表
                    base = settings.weather_proxy_refresh_seconds if refreshed else 300
                    next_refresh = now + base + random.uniform(0, base * 0.1)
                    next_probe = time.monotonic()
                elif low and now >= next_probe and self._unprobed():
                    # 低水位且手上还有没验过的候选：只探，不抓列表。
                    await self._probe_round()
                    self.report()
                    next_probe = time.monotonic() + settings.weather_proxy_topup_seconds
                self.save()
            except Exception:  # 后台故障不结束维护任务；不打印上游响应。
                log.exception("代理池刷新失败，保留已验证代理")
                next_refresh = time.monotonic() + 60
            await asyncio.sleep(30)

    async def _fetch_rows(self, proxy=None) -> list[str]:
        url = settings.weather_proxy_source or SOURCE
        params = None
        if "proxyscrape.com" in urlsplit(url).hostname or "":
            params = {
                "request": "display_proxies",
                "protocol": "http",
                "format": "json",
                "proxy_format": "protocolipport",
                "timeout": 3000,
                "ssl": "yes",
                "anonymity": "elite",
                "limit": settings.weather_proxy_candidates,
            }
        async with httpx.AsyncClient(  # noqa: SIM117
            proxy=proxy, timeout=8, trust_env=False, verify=True, follow_redirects=True
        ) as client:
            async with client.stream("GET", url, params=params) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 4_000_000:
                        raise ValueError("代理列表过大")
        rows = candidate_proxies(body.decode("utf8", "replace"))
        # 纯文本镜像有几千行且长期不变，固定取前 N 个等于所有人都用同一批死代理。
        random.shuffle(rows)
        return rows

    async def refresh(self) -> bool:
        # 官方接口境内可能无法直连；通过已有公网代理获取同一 HTTPS 列表。
        routes = [None] + [
            n.proxy
            for n in sorted(self.nodes.values(), key=lambda n: n.last_ok, reverse=True)
            if not n.busy and n.cooldown <= time.time()
        ][:2]
        rows: list = []
        refreshed = False
        for route in routes:
            try:
                async with asyncio.timeout(8):
                    rows = await self._fetch_rows(route)
                refreshed = True
                break
            except (httpx.HTTPError, TimeoutError, ValueError):
                log.info("代理列表获取失败 route=%s", route or "direct")
        self.last_refresh = (time.time(), refreshed)
        self._prune(refreshed)
        for proxy in rows:
            if len(self.nodes) >= settings.weather_proxy_candidates:
                break
            self.nodes.setdefault(proxy, Node(proxy))
        await self._probe_round()
        self.report()
        return refreshed

    def report(self) -> dict:
        """每轮把可用宽度与各出口用量落日志：这是唯一能看出池子够不够用的地方。"""
        stats = self.stats()
        log.info(
            "代理池 candidates=%s ready_nodes=%s ready_exits=%s paused_exits=%s usage=%s",
            stats["nodes"],
            stats["ready_nodes"],
            stats["ready_exits"],
            stats["paused_exits"],
            {ip: u["day"] for ip, u in stats["usage"].items() if u["day"]},
        )
        if stats["ready_exits"] < settings.weather_proxy_min_exits:
            # 降级事件要显式记一条，不能只体现为「偶尔 502」
            log.warning(
                "代理池可用出口不足 ready=%s target=%s candidates=%s",
                stats["ready_exits"],
                settings.weather_proxy_min_exits,
                stats["nodes"],
            )
        return stats

    def _prune(self, refreshed: bool):
        """淘汰连续失败过多或一天没成功的节点；列表没更新时不动已有条目。"""
        now = time.time()
        keep = {}
        for proxy, node in self.nodes.items():
            if (
                node.busy
                or not refreshed
                or node.streak < settings.weather_proxy_drop_streak
                and (now - node.last_ok < 86400 or node.cooldown > now or not node.last_ok)
            ):
                keep[proxy] = node
        self.nodes = keep
        alive = {n.exit_ip for n in self.nodes.values() if n.exit_ip}
        # 出口账本比节点活得久：同一个出口换个端口回来，不能借机清零用量。
        self.exits = {
            ip: e
            for ip, e in self.exits.items()
            if ip == DIRECT or ip in alive or e.cooldown > now or e.day.used(now)
        }

    def _unprobed(self) -> int:
        now = time.time()
        return sum(
            1 for n in self.nodes.values() if not n.usable(now) and not n.busy and n.cooldown <= now
        )

    async def _probe_round(self):
        """按预算检测候选：先测出口，再测目标服务。检测也计项目预算。"""
        now = time.time()
        candidates = [
            n
            for n in sorted(self.nodes.values(), key=lambda n: n.last_ok, reverse=True)
            if not n.usable(now) and not n.busy and n.cooldown <= now
        ][: settings.weather_proxy_probe_per_round]
        width = max(1, settings.weather_proxy_probe_concurrency)
        for offset in range(0, len(candidates), width):
            if self.ready_exits(time.time()) >= settings.weather_proxy_target_exits:
                break
            await asyncio.gather(*(self._probe(n) for n in candidates[offset : offset + width]))

    async def _probe(self, node: Node):
        start = time.monotonic()
        try:
            self._check_pause()
            if not await self._observe_exit(node):
                # 必须记一次失败：否则拿不到出口的候选没有冷却、streak 不涨，
                # 既永远不会被淘汰，又每轮都占着探测名额 —— 候选一多就全是它们。
                self._record(node, None, time.monotonic() - start, False)
                return
            item = self.exits.setdefault(node.exit_ip, Exit(node.exit_ip))
            if not item.free(time.time()) or not item.room(1, time.time()):
                return
            await shared.take(1)
            self._check_pause()
            item.charge(1, time.time())
            await self._request(node, PROBE, probe=True)
        except (
            UpstreamRateLimited,
            UpstreamUnavailable,
            ExitLimited,
            httpx.HTTPError,
            TimeoutError,
        ):
            pass

    async def _observe_exit(self, node: Node) -> bool:
        """查实际出口。首次入池要两次独立连接得到同一个 IP 才认。

        两次连接在**同一轮**里做完：一轮一次、下一轮再确认的话，冷启动要等满一个刷新
        周期（15 分钟）才有第一个可用出口，这期间所有气象请求都是 502。策略要求的是
        两次独立连接，不是两轮。
        """
        now = time.time()
        if node.exit_fresh(now):
            return True
        observed = await self._exit_ip(node)
        if observed is None:
            return False
        if not node.exit_ip:
            if node.exit_seen != observed:
                node.exit_seen = observed
                # 动态出口不会两次给同一个 IP，这一次就能判掉，不必等下一轮。
                again = await self._exit_ip(node)
                if again != observed:
                    return False
            node.exit_ip = observed
        elif node.exit_ip != observed:
            # 出口换了：旧出口的计数和冷却留着，节点重新关联。
            log.info("气象代理出口变化 proxy=%s %s -> %s", node.proxy, node.exit_ip, observed)
            node.exit_ip = observed
        node.exit_seen = observed
        node.exit_checked_at = now
        self.exits.setdefault(observed, Exit(observed))
        # 同出口最多留几个节点作连接备用，多的不再算作独立额度。
        same = [n for n in self.nodes.values() if n.exit_ip == observed]
        if len(same) > settings.weather_proxy_nodes_per_exit:
            for extra in sorted(same, key=lambda n: n.last_ok)[
                : len(same) - settings.weather_proxy_nodes_per_exit
            ]:
                if extra.proxy != node.proxy and not extra.busy:
                    self.nodes.pop(extra.proxy, None)
        return True

    async def _exit_ip(self, node: Node) -> str | None:
        url = settings.weather_proxy_exit_probe or EXIT_PROBE
        try:
            async with asyncio.timeout(settings.weather_proxy_exit_timeout):
                async with httpx.AsyncClient(
                    proxy=node.proxy,
                    timeout=settings.weather_proxy_exit_timeout,
                    trust_env=False,
                    verify=True,
                    follow_redirects=False,
                ) as client:
                    res = await client.get(url, params={"format": "json"})
            if res.status_code != 200:
                return None
            return parse_exit(res.text)
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
            return None

    # ---- 出网 ----

    def _check_pause(self):
        if self.paused_until > time.time() or shared.paused_until > time.monotonic():
            raise UpstreamRateLimited()

    def _record(self, node: Node, status: int | None, elapsed: float, ok: bool):
        node.status = status
        if ok:
            node.successes += 1
            node.streak = 0
            node.last_ok = time.time()
            node.latency = elapsed if node.successes == 1 else node.latency * 0.7 + elapsed * 0.3
        else:
            node.failures += 1
            node.streak += 1
            node.cooldown = time.time() + min(3600, 60 * 2 ** min(node.streak - 1, 6))
        log.info(
            "气象代理 proxy=%s exit=%s status=%s seconds=%.2f ok=%s",
            node.proxy,
            node.exit_ip or "-",
            status,
            elapsed,
            ok,
        )

    def _limited(self, node: Node, res: httpx.Response) -> str:
        """按 429 的窗口决定冷却范围：认得出就只停这个出口，认不出停整池。

        返回窗口名。分钟限流只是这个出口这一分钟满了，换个有余额的出口还能做；
        小时 / 日限流换出口也救不了当前这次请求，直接把 429 交回去。
        """
        now = time.time()
        body = res.text[:500]
        scope = scope_of(body)
        item = self.exits.get(node.exit_ip)
        if scope and item is not None:
            name, default = scope
            seconds = retry_seconds(res.headers.get("Retry-After"), default)
            item.pause(seconds, f"{name} 429", now)
            node.cooldown = max(node.cooldown, item.cooldown)
            log.warning(
                "气象代理出口限流 exit=%s scope=%s seconds=%.0f", node.exit_ip, name, seconds
            )
        else:
            # 来源不明：不靠换 IP 推断已解除，整池按共享冷却处理。
            shared.retry_after(res.headers.get("Retry-After"))
            self.paused_until = max(self.paused_until, now + shared.paused_until - time.monotonic())
            if item is not None:
                item.pause(self.paused_until - now, "429 来源不明", now)
            log.warning("气象代理限流来源不明 exit=%s body=%s", node.exit_ip, body[:120])
        self._record(node, 429, 0.0, False)
        self.save()
        return scope[0] if scope else "unknown"

    async def _request(self, node: Node, url: str, *, probe=False, **kwargs):
        timeout = settings.weather_proxy_probe_timeout if probe else self._timeout(kwargs)
        start = time.monotonic()
        node.busy += 1
        item = self.exits.get(node.exit_ip)
        if item is not None:
            item.busy += 1
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(
                    proxy=node.proxy,
                    timeout=timeout,
                    trust_env=False,
                    verify=True,
                    follow_redirects=False,
                ) as client:
                    res = await client.get(url, **kwargs)
            if res.status_code == 429:
                if self._limited(node, res) == "minute":
                    raise ExitLimited(res)
                return res
            if res.status_code == 400:
                node.status = 400
                log.info("气象代理 proxy=%s status=400，不重试", node.proxy)
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
            self._record(node, res.status_code, time.monotonic() - start, valid)
            if not valid:
                raise UpstreamUnavailable("气象代理未返回有效数据")
            return res
        except (httpx.TransportError, TimeoutError):
            self._record(node, None, time.monotonic() - start, False)
            raise
        finally:
            node.busy -= 1
            if item is not None:
                item.busy -= 1

    def _timeout(self, kwargs: dict) -> float:
        """调用方给的 timeout 只能收紧不能放宽，池自己的截止时间说了算。"""
        given = kwargs.pop("timeout", None)
        limit = settings.weather_proxy_request_timeout
        return min(float(given), limit) if isinstance(given, int | float) else limit

    def _pick(self, cost: float, now: float, skip: set[str], skip_exits: set[str]) -> Node | None:
        """出口先按最近最少使用挑，避免全部流量压在最快的一个出口上。"""
        ready = [
            n
            for n in self.nodes.values()
            if n.usable(now) and n.proxy not in skip and n.exit_ip not in skip_exits
        ]
        usable = [
            n
            for n in ready
            if (item := self.exits.get(n.exit_ip)) is not None
            and item.free(now)
            and item.room(cost, now)
        ]
        if not usable:
            return None
        return min(usable, key=lambda n: (self.exits[n.exit_ip].last_used, *n.score()))

    def _empty_reason(self) -> str:
        """空池错误带上计数与列表刷新结果，只有计数没有地址，可以直接给客户端看。"""
        now = time.time()
        when, ok = self.last_refresh
        age = f"{int(now - when)} 秒前" if when else "尚未进行"
        paused = sum(e.cooldown > now for e in self.exits.values())
        return (
            f"气象代理池暂无可用出口（候选 {len(self.nodes)}、"
            f"可用出口 {self.ready_exits(now)}、冷却中 {paused}；"
            f"上次列表刷新{age}{'成功' if ok else '失败'}），请稍后重试"
        )

    def direct(self) -> Exit:
        """直连出口的账本，常驻。"""
        return self.exits.setdefault(DIRECT, Exit(DIRECT))

    def direct_ready(self, cost: float) -> bool:
        """能不能退回直连：开关打开、不在冷却、三个窗口都还有余额。

        共享预算处于冷却时**不**兜底 —— 那是刻意设的保护，绕过去就没有意义了。
        """
        if not settings.weather_proxy_direct_fallback:
            return False
        if self.paused_until > time.time() or shared.paused_until > time.monotonic():
            return False
        now = time.time()
        item = self.direct()
        return item.cooldown <= now and item.room(cost, now)

    def charge_direct(self, cost: float) -> None:
        self.direct().charge(cost, time.time())

    def limit_direct(self, res: httpx.Response) -> str:
        """直连撞 429：按窗口停直连出口，与代理出口同一套归属规则。"""
        node = Node(DIRECT, exit_ip=DIRECT, exit_checked_at=time.time())
        return self._limited(node, res)

    async def get(self, url: str, *, background: bool = False, **kwargs) -> httpx.Response:
        try:
            async with asyncio.timeout(settings.weather_proxy_deadline):
                return await self._get(url, background=background, **kwargs)
        except TimeoutError as exc:
            raise UpstreamUnavailable("气象代理请求超时") from exc

    async def _get(self, url: str, *, background: bool, **kwargs) -> httpx.Response:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise UpstreamUnavailable("代理池只允许公开气象 HTTPS 地址")
        # 与应用共享客户端隔离：代理请求不继承令牌、Cookie、认证或环境代理。
        if set(kwargs) - {"params", "headers", "timeout"}:
            raise UpstreamUnavailable("代理池请求选项不受支持")
        kwargs["headers"] = {"Accept": "application/json"}
        cost = request_cost(kwargs.get("params") or {})
        limit = settings.weather_proxy_exit_units_per_minute
        if 0 < limit < cost:
            # 一批就超过出口分钟额度，等多久都发不出去。拆批是调用方的事
            # （全目录见 fleet_coords_per_request），这里直接报错，不要静默超发。
            raise UpstreamUnavailable(f"单批成本 {cost:.0f} 超过出口分钟额度 {limit:.0f}，请拆批")
        self._check_pause()
        async with self.background_slots() if background else _null(), self.slots():
            skip: set[str] = set()
            skip_exits: set[str] = set()
            limited: httpx.Response | None = None
            for attempt in range(max(1, settings.weather_proxy_attempts)):
                self._check_pause()
                if attempt:
                    # 换出口重试同样占项目预算，别让重试变成免费的。
                    await shared.take(cost)
                    self._check_pause()
                node = self._pick(cost, time.time(), skip, skip_exits)
                if node is None:
                    raise PoolExhausted(self._empty_reason())
                skip.add(node.proxy)
                skip_exits.add(node.exit_ip)
                self.exits[node.exit_ip].charge(cost, time.time())
                try:
                    log.info(
                        "气象代理请求 path=%s exit=%s cost=%.1f attempt=%s",
                        parsed.path,
                        node.exit_ip,
                        cost,
                        attempt + 1,
                    )
                    return await self._request(node, url, **dict(kwargs))
                except ExitLimited as exc:
                    limited = exc.response
                    continue
                except (httpx.TransportError, TimeoutError):
                    continue
            if limited is not None:
                return limited
            # 抛业务异常，避免 Provider 再乘以 upstream_retries。
            raise PoolExhausted("气象代理连接失败，请稍后重试")


class _null:
    """没开后台限名额时的占位，省掉一层分支。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


pool = ProxyPool()
