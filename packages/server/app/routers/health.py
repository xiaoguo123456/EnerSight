from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    server_time: str


@router.get("/health", response_model=Health)
async def health() -> Health:
    return Health(status="ok", server_time=datetime.now(UTC).isoformat())


@router.get("/ready", response_model=Health, include_in_schema=False)
async def ready(db: Annotated[AsyncSession, Depends(get_session)]) -> Health:
    """发布就绪检查必须验证数据库和迁移表，不能只检查进程存活。"""
    await db.execute(text("SELECT version_num FROM alembic_version"))
    return Health(status="ok", server_time=datetime.now(UTC).isoformat())
