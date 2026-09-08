"""开发态：小程序把客户端错误上报到服务端日志。仅 debug 模式挂载。"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/v1/debug", tags=["debug"])
log = logging.getLogger("app.client")


class ClientLog(BaseModel):
    level: str = "error"
    tag: str
    message: str
    extra: dict | None = None


@router.post("/log", status_code=204)
async def client_log(body: ClientLog) -> None:
    fn = log.error if body.level == "error" else log.info
    fn("[client:%s] %s %s", body.tag, body.message, body.extra or "")
