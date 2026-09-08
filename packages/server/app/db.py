"""数据库会话。

本地 SQLite、线上 PostgreSQL，通过 DATABASE_URL 切换。
时间字段一律存 UTC 的 naive datetime，出口再按站点时区格式化 —— 两种方言
对 timezone-aware 的处理不一致，统一成 naive UTC 最省事。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

pool_options = (
    {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
    }
    if settings.database_url.startswith("postgresql")
    else {}
)
engine = create_async_engine(settings.database_url, echo=settings.debug, **pool_options)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def upsert_insert(session: AsyncSession, model):
    """按实际连接选择冲突更新方言，开发 SQLite 与生产 PostgreSQL 共用业务逻辑。"""
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(model)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """naive UTC，见模块说明"""
    return datetime.now(UTC).replace(tzinfo=None)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
