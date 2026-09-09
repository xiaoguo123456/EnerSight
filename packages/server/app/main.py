"""FastAPI 应用入口。

接口契约见 docs/06。所有响应经 envelope 包装，坐标系由 coord 参数决定。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app import ratelimit, weather_model
from app.config import settings
from app.errors import ApiError, api_error_handler, validation_error_handler
from app.jobs import scheduler
from app.routers import (
    alerts,
    auth,
    catalog,
    geo,
    health,
    home,
    layers,
    reports,
    satellite,
    stations,
)


def _check_production_config() -> None:
    """非 debug 模式下带着开发默认值启动是事故，直接拒绝。"""
    if settings.debug:
        return
    if settings.jwt_secret.startswith("dev-only"):
        raise RuntimeError("生产环境必须设置 ENERSIGHT_JWT_SECRET")
    if settings.database_url.startswith("sqlite"):
        raise RuntimeError("生产环境不要用 SQLite，设置 ENERSIGHT_DATABASE_URL")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 应用自身的日志按 INFO 输出；uvicorn 只配了自己的 logger
    logging.getLogger("app").setLevel(logging.DEBUG if settings.debug else logging.INFO)
    if not logging.getLogger("app").handlers:
        logging.getLogger("app").addHandler(logging.StreamHandler())
    _check_production_config()
    app.state.http = httpx.AsyncClient(timeout=10.0)
    sched = scheduler.start(app) if settings.enable_scheduler else None
    try:
        yield
    finally:
        if sched:
            sched.shutdown(wait=False)
        await app.state.http.aclose()


app = FastAPI(
    title="EnerSight API",
    version="0.1.0",
    description="见 docs/06-api-contract.md",
    lifespan=lifespan,
    debug=settings.debug,
    root_path=settings.root_path,
)

# 小程序不走 CORS（request 是原生的），这是给 H5 预览用的；生产按域名收紧
if settings.debug:
    app.add_middleware(
        CORSMiddleware,
        # 本机任意端口：make preview 与 make shot 用不同端口
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost):\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.middleware("http")(ratelimit.middleware)
app.middleware("http")(weather_model.middleware)
app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(catalog.router)  # 必须在 stations 之前，否则 /catalog 被当成 station_id
app.include_router(stations.router)
app.include_router(home.router)
app.include_router(alerts.router)
app.include_router(reports.router)
app.include_router(geo.router)
app.include_router(layers.router)
app.include_router(satellite.router)
if settings.debug:
    from app.routers import debug as debug_router

    app.include_router(debug_router.router)

# 渲染好的图层图片。生产环境换成对象存储 + CDN，这里本地磁盘直出
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.render.tiles import tile_dir  # noqa: E402

app.mount("/tiles", StaticFiles(directory=str(tile_dir())), name="tiles")
