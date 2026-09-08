"""测试夹具：内存 SQLite + ASGI 客户端，每个测试独立库。"""

from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # StaticPool：内存库在多个连接间共享同一个 connection，否则每次 get_session 都是空库
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override() -> AsyncIterator:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    # ASGITransport 不触发 lifespan，手动准备 app.state.http（respx 会拦截它）
    app.state.http = httpx.AsyncClient(timeout=5.0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.http.aclose()
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _debug_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """开发态：不带 token 也能访问，登录直接签发 dev 用户。"""
    from app.config import settings

    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "wx_appid", "")
    monkeypatch.setattr(settings, "enable_scheduler", False)
