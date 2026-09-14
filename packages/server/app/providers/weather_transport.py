"""统一气象出网入口。未配置转发时直连；配置后白名单内的 Open-Meteo 接口经 Worker 转发。

公司网络要求业务服务器不直接以 IP 访问外部服务，部署见 docs/10「气象转发」。
请求预算、缓存与重试仍由调用方负责；转发失败不自动退回直连。
"""

import logging

import httpx

from app.config import settings
from app.errors import UpstreamUnavailable

log = logging.getLogger(__name__)

# 与 open_meteo.META_SLUGS 的取值、Worker 的 META 正则保持一致（有测试约束）。
META_SLUGS = frozenset({"ecmwf_ifs", "ncep_gfs013", "dwd_icon"})
# Worker 透传上游响应时带此头；没有它的响应是 Worker 自身的拒绝或错误。
RELAY_HEADER = "X-Weather-Relay"


def relay_path(url: str) -> str | None:
    """直连地址 → Worker 路径；不在白名单返回 None。"""
    if url == f"{settings.open_meteo_base}/forecast":
        return "/v1/forecast"
    if url == f"{settings.open_meteo_archive_base}/archive":
        return "/v1/archive"
    prefix = f"{settings.open_meteo_meta_base}/"
    if url.startswith(prefix):
        slug, _, rest = url[len(prefix) :].partition("/")
        if slug in META_SLUGS and rest == "static/meta.json":
            return f"/data/{slug}/static/meta.json"
    return None


async def weather_get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    base = settings.weather_relay_base
    if not base:
        return await client.get(url, **kwargs)
    path = relay_path(url)
    if path is None:
        raise UpstreamUnavailable("气象转发目标不受支持")
    token = settings.weather_relay_token.get_secret_value()
    if len(token) < 32:
        raise UpstreamUnavailable("气象转发配置不完整")
    headers = {**(kwargs.pop("headers", None) or {}), "Authorization": f"Bearer {token}"}
    res = await client.get(
        f"{base.rstrip('/')}{path}", headers=headers, follow_redirects=False, **kwargs
    )
    status = res.status_code
    # 上游的 400/429 带标记透传，交给调用方按原逻辑处理。无标记的 4xx 是 Worker 拒绝
    # （来源 IP、密钥、参数白名单），重试无用；5xx 原样返回，由调用方决定是否重试。
    relayed = res.headers.get(RELAY_HEADER) == "cloudflare"
    if 300 <= status < 400 or (400 <= status < 500 and not relayed):
        log.warning("weather relay rejected: status=%s path=%s", status, path)
        raise UpstreamUnavailable("气象转发服务拒绝请求")
    return res
