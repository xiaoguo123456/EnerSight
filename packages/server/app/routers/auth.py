"""登录。docs/06 §四

真正的微信登录：code → code2session → openid → JWT。需要 AppID/AppSecret。
开发态（debug 且未配 AppID）直接签发开发用户 token。
"""

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.auth import DEV_USER_ID, dev_login_enabled, issue_token
from app.config import settings
from app.errors import ApiError
from app.schemas.common import Coord
from app.schemas.envelope import Envelope, envelope

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    code: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int


@router.post("/login", response_model=Envelope[LoginResponse])
async def login(body: LoginRequest, request: Request) -> Envelope[LoginResponse]:
    if dev_login_enabled():
        token, ttl = issue_token(DEV_USER_ID)
        return envelope(LoginResponse(token=token, expires_in=ttl), Coord.WGS84)
    if not settings.wx_appid or not settings.wx_secret:
        raise ApiError("LOGIN_UNAVAILABLE", "微信登录尚未配置", 503)
    if not body.code.strip() or len(body.code) > 512:
        raise ApiError("INVALID_CODE", "登录凭证无效，请重新登录", 400)
    try:
        response = await request.app.state.http.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": settings.wx_appid,
                "secret": settings.wx_secret,
                "js_code": body.code,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        # 不向客户端或日志暴露含 AppSecret 的上游 URL、响应和异常。
        raise ApiError("LOGIN_UNAVAILABLE", "微信登录暂不可用，请稍后重试", 503) from None
    if not isinstance(data, dict):
        raise ApiError("LOGIN_UNAVAILABLE", "微信登录响应异常", 503)
    if data.get("errcode") in (40029, 40163):
        raise ApiError("INVALID_CODE", "登录凭证已失效，请重新登录", 400)
    openid = data.get("openid")
    if data.get("errcode", 0) != 0 or not isinstance(openid, str) or not 1 <= len(openid) <= 64:
        raise ApiError("LOGIN_UNAVAILABLE", "微信登录暂不可用，请稍后重试", 503)
    token, ttl = issue_token(openid)
    return envelope(LoginResponse(token=token, expires_in=ttl), Coord.WGS84)
