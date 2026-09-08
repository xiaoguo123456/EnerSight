"""登录。docs/06 §四

真正的微信登录：code → code2session → openid → JWT。需要 AppID/AppSecret。
开发态（debug 且未配 AppID）直接签发开发用户 token。
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.auth import DEV_USER_ID, dev_login_enabled, issue_token
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
async def login(body: LoginRequest) -> Envelope[LoginResponse]:
    if dev_login_enabled():
        token, ttl = issue_token(DEV_USER_ID)
        return envelope(LoginResponse(token=token, expires_in=ttl), Coord.WGS84)
    # TODO: 接入 code2session，需 ENERSIGHT_WX_APPID / ENERSIGHT_WX_SECRET
    raise ApiError("LOGIN_UNAVAILABLE", "微信登录尚未配置", 503)
