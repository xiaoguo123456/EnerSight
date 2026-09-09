"""按请求选择预报模型；公共预警与后台任务始终使用统一默认模型。"""

from contextvars import ContextVar

from starlette.responses import JSONResponse

MODELS = {"best_match", "ecmwf_ifs", "gfs_global", "icon_global"}
current_model: ContextVar[str] = ContextVar("weather_model", default="best_match")


def supports_selection(path: str) -> bool:
    path = "/v1/" + path.split("/v1/", 1)[-1]
    return (
        path in {"/v1/home", "/v1/trends", "/v1/map/overview"}
        or path.startswith("/v1/map/layers/")
        or (path.startswith("/v1/stations/") and path.endswith("/detail"))
    )


async def middleware(request, call_next):
    selected = request.headers.get("X-Weather-Model", "best_match")
    if selected not in MODELS:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_MODEL", "message": "不支持的气象模型"}},
        )
    token = current_model.set(selected if supports_selection(request.url.path) else "best_match")
    try:
        response = await call_next(request)
        response.headers["X-Weather-Model"] = current_model.get()
        return response
    finally:
        current_model.reset(token)
