"""目录导入：GEM 表解析、多期合并、同址去重、退役标记。"""

from pathlib import Path

import openpyxl

from app.catalog import importer
from app.models import CatalogPlant

GEM_HEADERS = [
    "Country/Area",
    "Project Name",
    "Phase Name",
    "Project Name in Local Language / Script",
    "Capacity (MW)",
    "Status",
    "Start year",
    "Owner",
    "Owner Name in Local Language / Script",
    "Latitude",
    "Longitude",
    "State/Province",
    "Major area (prefecture, district)",
    "Local area (taluk, county)",
    "GEM location ID",
    "GEM phase ID",
]


def _gem_xlsx(path: Path, rows: list[list]) -> Path:
    wb = openpyxl.Workbook()
    about = wb.active
    about.title = "About"
    about.append(["说明"])
    ws = wb.create_sheet("Data")
    ws.append(GEM_HEADERS)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


class TestReadGem:
    def test_解析_合并多期_过滤非运行(self, tmp_path):
        rows = [
            [
                "China",
                "Guazhou Wind",
                "Phase 1",
                "瓜州风电场",
                100,
                "operating",
                2018,
                "Huaneng",
                "华能",
                40.52,
                95.78,
                "Gansu",
                "Jiuquan",
                "Guazhou",
                "L1",
                "G1",
            ],
            [
                "China",
                "Guazhou Wind",
                "Phase 2",
                "瓜州风电场",
                150,
                "operating",
                2020,
                "Huaneng",
                "华能",
                40.52,
                95.78,
                "Gansu",
                "Jiuquan",
                "Guazhou",
                "L1",
                "G2",
            ],
            [
                "China",
                "Planned Wind",
                "--",
                "规划风电",
                50,
                "pre-construction",
                None,
                None,
                None,
                41.0,
                96.0,
                "Gansu",
                None,
                None,
                "L2",
                "G3",
            ],
            [
                "Japan",
                "Hokkaido Wind",
                "--",
                None,
                30,
                "operating",
                2019,
                None,
                None,
                43.0,
                141.0,
                "Hokkaido",
                None,
                None,
                "L3",
                "G4",
            ],
            [
                "China",
                "Tiny",
                "--",
                None,
                0.5,
                "operating",
                2019,
                None,
                None,
                42.0,
                97.0,
                "Gansu",
                None,
                None,
                "L4",
                "G5",
            ],
        ]
        rs = importer.read_gem(_gem_xlsx(tmp_path / "Global-Wind-Power-Tracker.xlsx", rows), "CHN")
        assert len(rs) == 1
        r = rs[0]
        assert r.id == "gem:L1" and r.type == "wind"
        assert r.capacity_mw == 250 and r.name_local == "瓜州风电场"
        assert (
            r.province == "甘肃省" and r.city == "Jiuquan" and r.owner == "华能" and r.year == 2018
        )


class TestUpsert:
    async def test_同址GEM覆盖WRI_并可标记退役(self, client):
        from app.db import get_session
        from app.main import app

        gen = app.dependency_overrides[get_session]()
        db = await gen.__anext__()
        wri = importer.Row("wri", "W1", "Guazhou", None, "wind", 100, 40.521, 95.781)
        await importer.upsert(db, [wri])
        await db.commit()
        gem = importer.Row(
            "gem", "L1", "Guazhou Wind", "瓜州风电场", "wind", 250, 40.52, 95.78, province="甘肃省"
        )
        res = await importer.upsert(db, [gem])
        await db.commit()
        assert (res.added, res.replaced) == (1, 1)
        assert await db.get(CatalogPlant, "wri:W1") is None  # 同址 1 km 内被 GEM 替换
        assert (await db.get(CatalogPlant, "gem:L1")).display_name == "瓜州风电场"

        # 下次同步没出现 → 退役；再出现 → 恢复为 operating（upsert 覆盖 status）
        assert await importer.retire_missing(db, "gem", "wind", set()) == 1
        await db.commit()
        assert (await db.get(CatalogPlant, "gem:L1")).status == "retired"
        await importer.upsert(db, [gem])
        await db.commit()
        assert (await db.get(CatalogPlant, "gem:L1")).status == "operating"
        await gen.aclose()

    def test_名字清洗(self):
        assert importer.clean_name("Qili} Dunhuang  SunCan") == "Qili Dunhuang SunCan"
        assert importer.first_name("瓜州宝丰风电项目, 瓜州宝丰风电项目") == "瓜州宝丰风电项目"
        assert importer.first_name("华能一期; 华能二期") == "华能一期"

    def test_地址拼接(self):
        from app.services.catalog import join_address

        assert join_address("甘肃省", "Jiuquan", "Guazhou") == "甘肃省 Jiuquan Guazhou"
        assert join_address("甘肃省", "酒泉市", "瓜州县") == "甘肃省酒泉市瓜州县"
        assert join_address(None, "", None) is None
