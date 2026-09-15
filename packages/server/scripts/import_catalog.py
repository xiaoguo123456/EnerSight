"""导入公开电站目录（CLI）。解析与写库逻辑在 app/catalog/importer.py。docs/04 §七

用法：
  uv run python scripts/import_catalog.py wri /path/to/global_power_plant_database.csv
  uv run python scripts/import_catalog.py gem /path/to/Global-Solar-Power-Tracker.xlsx
  uv run python scripts/import_catalog.py gem /path/to/Global-Wind-Power-Tracker.xlsx
  加 --country CHN 只导中国（默认）；--all 导全部国家
  GEM 导入后会把该类型里这次没出现的 GEM 条目标记 retired。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalog import importer, quality  # noqa: E402
from app.db import SessionLocal  # noqa: E402


async def run(source: str, path: Path, country: str | None) -> None:
    rows = importer.read_wri(path, country) if source == "wri" else importer.read_gem(path, country)
    n_solar = sum(1 for r in rows if r.type == "solar")
    print(f"读到 {len(rows)} 条（光伏 {n_solar}，风电 {len(rows) - n_solar}），写库 ...")
    async with SessionLocal() as db:
        res = await importer.upsert(db, rows)
        retired = 0
        if source == "gem" and rows:
            types = {r.type for r in rows}
            for t in types:
                retired += await importer.retire_missing(db, "gem", t, res.seen_ids or set())
        # 占位坐标、WRI 重复、合并场址命名，每次导入后重算，见 app/catalog/quality.py
        report = await quality.apply_db(db)
        await db.commit()
    print(f"新增 {res.added}，更新 {res.updated}，同址去重 {res.replaced}，标记退役 {retired}")
    print(f"数据质量：{report}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=["wri", "gem"])
    ap.add_argument("path", type=Path)
    ap.add_argument("--country", default="CHN", help="ISO3，默认 CHN；--all 导全部")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.source, args.path, None if args.all else args.country))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
