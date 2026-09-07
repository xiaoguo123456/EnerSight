"""FastAPI 应用入口。

接口契约见 docs/06。所有响应经 envelope 包装，坐标系由 coord 参数决定。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import settings
from app.errors import ApiError, api_error_handler
from app.routers import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.http = httpx.AsyncClient(timeout=10.0)
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(
    title="EnerSight API",
    version="0.1.0",
    description="见 docs/06-api-contract.md",
    lifespan=lifespan,
    debug=settings.debug,
)

app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
app.include_router(health.router)
