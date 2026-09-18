"""按请求选择预报模型；公共预警与后台任务始终使用统一默认模型。

`current_model` **永远是一个真实的上游模型名** —— 它会被直接当作 Open-Meteo 的 `models`
参数发出去，也是单点缓存键的一部分。三模式区间不是一个上游模型，而是路由层的模式：
命中时 `request.state.ensemble` 为真，由 `services.ensemble` 逐个成员设置真实模型并发计算。
docs/19 §一
"""

from contextvars import ContextVar

from starlette.responses import JSONResponse

MODELS = {"best_match", "ecmwf_ifs", "gfs_global", "icon_global"}
ENSEMBLE = "ensemble"
SELECTABLE = MODELS | {ENSEMBLE}
current_model: ContextVar[str] = ContextVar("weather_model", default="best_match")


def _path(path: str) -> str:
    return "/v1/" + path.split("/v1/", 1)[-1]


def supports_selection(path: str) -> bool:
    path = _path(path)
    return (
        path
        in {
            "/v1/home",
            "/v1/trends",
            "/v1/map/overview",
            "/v1/predictions/fleet",
            "/v1/predictions/fleet/history",
            "/v1/predictions/station",
        }
        or path.startswith("/v1/map/layers/")
        or (path.startswith("/v1/stations/") and path.endswith("/detail"))
    )


def supports_ensemble(path: str) -> bool:
    """只有 7 天预测走三模式：首页预测卡的主数字取自它，首屏不必等三家。docs/19 §一"""
    return _path(path) == "/v1/predictions/station"


async def middleware(request, call_next):
    raw = request.query_params.get("weather_model") or request.headers.get("X-Weather-Model")
    path = request.url.path
    if raw is None:
        # 不传即默认：7 天预测三模式，其余自动选择
        raw = ENSEMBLE if supports_ensemble(path) else "best_match"
    if raw not in SELECTABLE:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_MODEL", "message": "不支持的气象模型"}},
        )
    ensemble = raw == ENSEMBLE and supports_ensemble(path)
    request.state.ensemble = ensemble
    # ensemble 不是上游模型名，落到 ContextVar 前换成自动选择
    selected = "best_match" if raw == ENSEMBLE else raw
    token = current_model.set(selected if supports_selection(path) else "best_match")
    try:
        response = await call_next(request)
        response.headers["X-Weather-Model"] = ENSEMBLE if ensemble else current_model.get()
        return response
    finally:
        current_model.reset(token)
