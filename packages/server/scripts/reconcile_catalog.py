"""从已下载原始表回填分期；默认仅预览，--apply 后更新来源与合计名称，保留 ID。"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select

from app.catalog.importer import read_gem
from app.db import SessionLocal
from app.models import CatalogPlant


async def run(args):
    rows = read_gem(Path(args.file), "CHN")
    digest = hashlib.sha256(Path(args.file).read_bytes()).hexdigest()
    changed = missing = 0
    examples = []
    async with SessionLocal() as db:
        existing = {p.id: p for p in (await db.execute(select(CatalogPlant))).scalars()}
        for row in rows:
            p = existing.get(row.id)
            if not p:
                missing += 1
                continue
            if abs(p.capacity_kw - row.capacity_mw * 1000) > 0.01:
                raise ValueError(f"{row.id} 原始容量与库内不一致，停止自动回填，需核验")
            changed += 1
            if row.id in ("gem:L100001016469", "gem:L100000917373"):
                examples.append(
                    {"id": row.id, "name": row.name_local, "phases": row.provenance["phases"]}
                )
            if args.apply:
                p.name_local = row.name_local
                p.provenance = dict(row.provenance, source_sha256=digest)
                if len(row.provenance["phases"]) > 1:
                    p.owner_name = None  # 多业主信息逐期保留，不代表全部容量
        if args.apply:
            await db.commit()
    print(
        json.dumps(
            {
                "apply": args.apply,
                "matched": changed,
                "missing": missing,
                "source_sha256": digest,
                "examples": examples,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args()))
