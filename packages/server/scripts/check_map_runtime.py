"""镜像发布前验证 Linux 动态库、COG、工作进程以及应用启动，不连接外部服务。"""

import asyncio
import tempfile
from pathlib import Path

import numpy as np
from rasterio.transform import from_bounds

from app.config import settings
from app.jobs import map_prepare
from app.main import app
from app.render import map_raster


async def main():
    settings.debug = True
    settings.enable_scheduler = True
    with tempfile.TemporaryDirectory() as directory:
        cog, png = Path(directory) / "check.tif", Path(directory) / "check.png"
        map_raster._write_cog(
            cog,
            np.full((1, 64, 64), 20, dtype="float32"),
            from_bounds(60, 0, 150, 65, 64, 64),
        )
        async with app.router.lifespan_context(app):
            await map_prepare.run(map_raster.render_tile, cog, png, "temperature", 3, 5, 2)
            assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    print("地图镜像运行检查通过：COG、子进程切片、应用启动")


if __name__ == "__main__":
    asyncio.run(main())
