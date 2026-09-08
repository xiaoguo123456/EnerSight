"""对已迁移的专用测试库验证真实 PostgreSQL upsert；整个事务回滚，不碰生产。"""

import asyncio
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import engine, upsert_insert
from app.models import DailyGeneration, Report
from app.services.accumulate import upsert_daily


async def main() -> None:
    if not settings.database_url.endswith("/enersight_test"):
        raise RuntimeError("此脚本只允许连接 enersight_test 测试库")
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                day = date(2026, 1, 1)
                await upsert_daily(session, "pg-smoke", day, 10, 2)
                await upsert_daily(session, "pg-smoke", day, 20, 3)
                row = (
                    await session.execute(
                        select(DailyGeneration).where(DailyGeneration.station_id == "pg-smoke")
                    )
                ).scalar_one()
                assert row.kwh == 20 and row.current_kw == 3
                row.source = "measured"
                await session.flush()
                await upsert_daily(session, "pg-smoke", day, 99, 9)
                await session.refresh(row)
                assert row.kwh == 20, "预测不能覆盖实测"
                for value in [1, 2]:
                    statement = upsert_insert(session, Report).values(
                        station_id="pg-smoke",
                        day=day,
                        content={"value": value},
                        summary={},
                        provider="rule",
                        is_fallback=False,
                        prompt_input="测试",
                    )
                    await session.execute(
                        statement.on_conflict_do_update(
                            index_elements=["station_id", "day"],
                            set_={"content": statement.excluded.content},
                        )
                    )
                count = (
                    await session.execute(
                        select(func.count())
                        .select_from(Report)
                        .where(Report.station_id == "pg-smoke")
                    )
                ).scalar_one()
                report = (
                    await session.execute(select(Report).where(Report.station_id == "pg-smoke"))
                ).scalar_one()
                assert count == 1 and report.content == {"value": 2}
        finally:
            await transaction.rollback()
    await engine.dispose()
    print("PostgreSQL 迁移后重复写入、JSON 更新和实测保护检查通过，数据已回滚。")


if __name__ == "__main__":
    asyncio.run(main())
