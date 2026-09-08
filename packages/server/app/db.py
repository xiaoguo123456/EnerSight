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

engine = create_async_engine(settings.database_url, echo=settings.debug)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """naive UTC，见模块说明"""
    return datetime.now(UTC).replace(tzinfo=None)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
