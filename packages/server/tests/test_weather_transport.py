"""气象出网：未配置时直连；配置转发后只放行白名单接口，密钥只发给 Worker。"""

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from app.config import Settings, settings
from app.errors import UpstreamUnavailable
from app.providers import open_meteo
from app.providers.weather_transport import META_SLUGS, weather_get

RELAY = "https://relay.example.com"
TOKEN = "t" * 64
RELAYED = {"X-Weather-Relay": "cloudflare"}


@pytest.fixture
def relay(monkeypatch):
    monkeypatch.setattr(settings, "weather_relay_base", RELAY)
    monkeypatch.setattr(settings, "weather_relay_token", SecretStr(TOKEN))


def forecast_url() -> str:
    return f"{settings.open_meteo_base}/forecast"


async def test_未配置转发时直连且不带密钥(monkeypatch):
    monkeypatch.setattr(settings, "weather_relay_base", "")
    async with httpx.AsyncClient() as http:
        with respx.mock:
            route = respx.get(forecast_url()).mock(return_value=httpx.Response(200, json={}))
            res = await weather_get(http, forecast_url(), params={"latitude": 39.9})
    assert res.status_code == 200
    assert route.calls[0].request.url.params["latitude"] == "39.9"
    assert "authorization" not in route.calls[0].request.headers


@pytest.mark.parametrize(
    "setting,suffix,path",
    [
        ("open_meteo_base", "/forecast", "/v1/forecast"),
        ("open_meteo_archive_base", "/archive", "/v1/archive"),
        ("open_meteo_meta_base", "/dwd_icon/static/meta.json", "/data/dwd_icon/static/meta.json"),
    ],
)
async def test_白名单接口转发到Worker并带密钥(relay, setting, suffix, path):
    url = getattr(settings, setting) + suffix
    async with httpx.AsyncClient() as http:
        with respx.mock:
            route = respx.get(RELAY + path).mock(
                return_value=httpx.Response(200, json={}, headers=RELAYED)
            )
            res = await weather_get(
                http, url, params={"latitude": "39.9,31.2"}, headers={"Accept": "application/json"}
            )
    request = route.calls[0].request
    assert res.status_code == 200
    assert request.url.params["latitude"] == "39.9,31.2"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "application/json"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1/forecast",
        "{forecast}/../elevation",
        "{meta}/unknown_model/static/meta.json",
        "{meta}/dwd_icon/static/other.json",
    ],
)
async def test_白名单外地址不发请求(relay, url):
    url = url.format(forecast=forecast_url(), meta=settings.open_meteo_meta_base)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            with pytest.raises(UpstreamUnavailable):
                await weather_get(http, url)
            assert not respx.calls


async def test_密钥不足32位不发请求(relay, monkeypatch):
    monkeypatch.setattr(settings, "weather_relay_token", SecretStr("short"))
    async with httpx.AsyncClient() as http:
        with respx.mock:
            with pytest.raises(UpstreamUnavailable):
                await weather_get(http, forecast_url())
            assert not respx.calls


@pytest.mark.parametrize("status", [302, 400, 401, 403, 405])
async def test_Worker自身拒绝判为转发异常(relay, status):
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(RELAY + "/v1/forecast").mock(return_value=httpx.Response(status))
            with pytest.raises(UpstreamUnavailable):
                await weather_get(http, forecast_url())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, headers={**RELAYED, "Retry-After": "60"}),
        httpx.Response(400, headers=RELAYED),
        httpx.Response(502),  # Worker 连接上游失败，交给调用方按 5xx 重试
    ],
)
async def test_上游响应与5xx原样交给调用方(relay, response):
    async with httpx.AsyncClient() as http:
        with respx.mock:
            respx.get(RELAY + "/v1/forecast").mock(return_value=response)
            res = await weather_get(http, forecast_url())
    assert res.status_code == response.status_code
    assert res.headers.get("retry-after") == response.headers.get("retry-after")


async def test_点预报遇Worker拒绝不重试也不当作坐标越界(relay, monkeypatch):
    async def free(_cost):
        return None

    monkeypatch.setattr(open_meteo.shared, "take", free)
    async with httpx.AsyncClient() as http:
        with respx.mock:
            route = respx.get(RELAY + "/v1/forecast").mock(
                return_value=httpx.Response(400, json={"reason": "不支持的气象请求"})
            )
            with pytest.raises(UpstreamUnavailable):
                await open_meteo.OpenMeteoProvider(http).forecast(39.9, 116.4)
    assert route.call_count == 1


def test_元数据白名单与模型映射一致():
    assert set(open_meteo.META_SLUGS.values()) == META_SLUGS


@pytest.mark.parametrize(
    "base",
    [
        "http://relay.example.com",
        "relay.example.com",
        "https://relay.example.com/v1",
        "https://relay.example.com?x=1",
        "https://relay.example.com#top",
        "https://user:pass@relay.example.com",
    ],
)
def test_转发地址必须是纯HTTPS域名(base):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, weather_relay_base=base, weather_relay_token=TOKEN)


def test_启用转发必须配置足够长的密钥():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, weather_relay_base=RELAY, weather_relay_token="short")


def test_合法转发配置与未启用转发都能加载():
    assert Settings(_env_file=None, weather_relay_base=RELAY + "/", weather_relay_token=TOKEN)
    assert Settings(_env_file=None, weather_relay_base="", weather_relay_token="")
