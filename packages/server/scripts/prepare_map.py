"""手动预热气象栅格：uv run python -m scripts.prepare_map --hours 2。"""

import argparse
import asyncio
import logging

import httpx

from app.jobs import map_prepare


async def main(hours):
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            await map_prepare.refresh(http, hours)
    finally:
        await map_prepare.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="预处理当前及未来小时的气象地图")
    parser.add_argument("--hours", type=int, choices=range(3), default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(args.hours))
