"""数据库会话。

本地 SQLite、线上 PostgreSQL，通过 DATABASE_URL 切换。
时间字段一律存 UTC 的 naive datetime，出口再按站点时区格式化 —— 两种方言
对 timezone-aware 的处理不一致，统一成 naive UTC 最省事。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select
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


async def pages(db: AsyncSession, stmt, entity, size: int) -> AsyncIterator[list]:
    """按主键 keyset 翻页遍历大表，复用调用方的会话。

    全表一次 `.all()` 在几万行上是定时炸弹；offset 分页在深页会退化成全表扫描，
    并发写入时还会跳行。主键是有序字符串，`id > last` 最省事也最稳。
    """
    last = ""
    while True:
        page = list(
            (await db.execute(stmt.where(entity.id > last).order_by(entity.id).limit(size)))
            .scalars()
            .all()
        )
        if not page:
            return
        last = page[-1].id  # 在 yield（以及随后可能的 commit）之前取，不依赖 expire_on_commit
        yield page


async def id_pages(entity, size: int) -> AsyncIterator[list[str]]:
    """同样是 keyset 翻页，但每页单独开一次会话，只取主键。

    给「一任务一会话」的并发场景用：线上连接预算一共 3 个
    （`db_pool_size` + `db_max_overflow`），翻页会话不该一直占着一个跟工作任务抢。
    """
    last = ""
    while True:
        async with SessionLocal() as db:
            ids = list(
                (
                    await db.execute(
                        select(entity.id).where(entity.id > last).order_by(entity.id).limit(size)
                    )
                )
                .scalars()
                .all()
            )
        if not ids:
            return
        last = ids[-1]
        yield ids


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
