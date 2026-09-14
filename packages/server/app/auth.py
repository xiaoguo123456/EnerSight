"""鉴权。

开发态：debug=True 且未配置微信 AppID 时，登录接口直接签发开发用户的 token，
不去调微信 —— 真正的 code2session 依赖 AppID/AppSecret，那是合规那条线的事，
不让它卡住开发。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, Header

from app.config import settings
from app.errors import ApiError

DEV_USER_ID = "dev-user"


@dataclass(frozen=True)
class CurrentUser:
    id: str


def issue_token(user_id: str) -> tuple[str, int]:
    """返回 (token, expires_in 秒)"""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_expires_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), settings.jwt_expires_seconds


def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        # 前端 core/api 收到这个 code 会自动重登重试一次。docs/06 §4.2
        raise ApiError("TOKEN_EXPIRED", "登录已过期", 401) from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError("UNAUTHORIZED", "未登录", 401) from exc
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise ApiError("UNAUTHORIZED", "未登录", 401)
    return sub


def dev_login_enabled() -> bool:
    return settings.debug and not settings.wx_appid


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        # 开发态允许不带 token 直接访问，省得每次联调都先登录
        if dev_login_enabled():
            return CurrentUser(id=DEV_USER_ID)
        raise ApiError("UNAUTHORIZED", "未登录", 401)
    return CurrentUser(id=decode_token(authorization[7:].strip()))


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def get_optional_user(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser | None:
    """公开数据游客可访问：不带 token 视为游客。带了 token 仍按 token 校验，
    失效返回 401，让已登录用户续登后重试。docs/09 §4.3
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return CurrentUser(id=DEV_USER_ID) if dev_login_enabled() else None
    return CurrentUser(id=decode_token(authorization[7:].strip()))


OptionalUserDep = Annotated[CurrentUser | None, Depends(get_optional_user)]


def owner_of(user: CurrentUser | None) -> str | None:
    return user.id if user else None
