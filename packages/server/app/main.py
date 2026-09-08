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

from app.config import settings
from app.errors import ApiError, api_error_handler, validation_error_handler
from app.jobs import scheduler
from app.routers import alerts, auth, health, home, reports, stations


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
)

# 小程序不走 CORS（request 是原生的），这是给 H5 预览用的；生产按域名收紧
if settings.debug:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:4173", "http://localhost:4173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(stations.router)
app.include_router(home.router)
app.include_router(alerts.router)
app.include_router(reports.router)
