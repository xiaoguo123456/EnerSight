"""真实登录协议使用模拟微信响应，检查身份签发与失败时拒绝登录。"""

import httpx
import pytest
import respx

from app.auth import decode_token
from app.config import settings


@pytest.fixture
def production_login(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "wx_appid", "wx-test")
    monkeypatch.setattr(settings, "wx_secret", "test-private-secret")


async def test_微信身份签发且不返回会话密钥(client, production_login):
    with respx.mock:
        route = respx.get("https://api.weixin.qq.com/sns/jscode2session").mock(
            return_value=httpx.Response(
                200, json={"openid": "real-user", "session_key": "private-session"}
            )
        )
        response = await client.post("/v1/auth/login", json={"code": "fresh-code"})
    assert response.status_code == 200
    assert decode_token(response.json()["data"]["token"]) == "real-user"
    assert route.calls[0].request.url.params["js_code"] == "fresh-code"
    assert "private-session" not in response.text and "test-private-secret" not in response.text


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"errcode": 40029}, 400),
        ({"errcode": 40163}, 400),
        ({"errcode": -1}, 503),
        ({"openid": ""}, 503),
        ([], 503),
    ],
)
async def test_异常响应不签发令牌(client, production_login, payload, status):
    with respx.mock:
        respx.get("https://api.weixin.qq.com/sns/jscode2session").mock(
            return_value=httpx.Response(200, json=payload)
        )
        response = await client.post("/v1/auth/login", json={"code": "test-code"})
    assert response.status_code == status
    assert "token" not in response.json().get("data", {})


async def test_超时不泄露上游密钥(client, production_login):
    with respx.mock:
        respx.get("https://api.weixin.qq.com/sns/jscode2session").mock(
            side_effect=httpx.ConnectTimeout("secret=test-private-secret")
        )
        response = await client.post("/v1/auth/login", json={"code": "test-code"})
    assert response.status_code == 503 and "test-private-secret" not in response.text
