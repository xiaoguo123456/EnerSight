"""统一错误响应。错误码表见 docs/06 §十三。"""

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


# 502 上游故障可重试；503 该位置本就无数据，重试无用
class UpstreamUnavailable(ApiError):
    def __init__(self, message: str = "上游数据源暂时不可用") -> None:
        super().__init__("UPSTREAM_UNAVAILABLE", message, 502)


class DataUnavailable(ApiError):
    def __init__(self, message: str = "该区域暂无数据") -> None:
        super().__init__("DATA_UNAVAILABLE", message, 503)


class StationNotFound(ApiError):
    def __init__(self) -> None:
        super().__init__("STATION_NOT_FOUND", "站点不存在", 404)


class InvalidCoordinate(ApiError):
    def __init__(self) -> None:
        super().__init__("INVALID_COORDINATE", "经纬度超出合法范围", 400)


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )
