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


@router.get("/weather-upstream", include_in_schema=False)
async def weather_upstream() -> dict:
    """自建气象实例的主源/兜底状态与熔断计数。

    只在 debug 模式挂载 —— 线上（含测试环境）`ENERSIGHT_DEBUG=false`，
    看 `weather_upstream:` 那条定时日志，别把这个挂到公网前缀下。
    """
    from app.providers import weather_transport

    return weather_transport.status()
