"""目录数据质量：占位坐标、WRI 重复与省份、合并场址命名、同名消歧、汇总去重。docs/04 §七"""

import pytest

from app.catalog import quality
from app.models import CatalogPlant
from app.services.fleet_prediction import eligible
from app.services.prediction_basis import catalog_basis

PLACEHOLDER = (43.2443, 114.3252)


def gem(
    id,
    lat,
    lon,
    *,
    kind="wind",
    city=None,
    district=None,
    province="内蒙古自治区",
    capacity=50_000,
    name="Wind farm",
    local=None,
    phases=None,
):
    return CatalogPlant(
        id=id,
        source="gem",
        source_id=id,
        type=kind,
        name=name,
        name_local=local,
        latitude=lat,
        longitude=lon,
        capacity_kw=capacity,
        province=province,
        city=city,
        district=district,
        status="operating",
        provenance={"phases": phases or [{"id": f"G-{id}", "capacity_kw": capacity}]},
    )


def wri(id, lat, lon, *, kind="wind", capacity=49_000):
    return CatalogPlant(
        id=id,
        source="wri",
        source_id=id,
        type=kind,
        name=id,
        latitude=lat,
        longitude=lon,
        capacity_kw=capacity,
        status="operating",
    )


def placeholder_case():
    return [
        # 同一坐标、跨三个市：省级占位
        gem("p1", *PLACEHOLDER, city="Baotou", district="Guyang"),
        gem("p2", *PLACEHOLDER, city="Chifeng", district="Hexigten"),
        gem("p3", *PLACEHOLDER, city="Xilingol", district=None),
        # 固阳县两座坐标可靠的场站
        gem("r1", 41.20, 109.80, city="Baotou", district="Guyang"),
        gem("r2", 41.40, 110.00, city="Baotou", district="Guyang"),
        # 赤峰市只有别的县有可靠场站
        gem("r3", 42.30, 118.90, city="Chifeng", district="Hongshan"),
    ]


class TestLocation:
    def test_省级占位改用同县再同市的可靠点_找不到的不参与预测(self):
        plants = placeholder_case()
        report = quality.apply(plants)
        p1, p2, p3 = plants[:3]
        assert (p1.latitude, p1.longitude) == (41.3, 109.9)  # 同县两点中位
        assert p1.provenance["location"]["method"] == "district"
        assert (p2.latitude, p2.longitude) == (42.3, 118.9)  # 同市兜底
        assert p2.provenance["location"]["method"] == "city"
        assert (p3.latitude, p3.longitude) == PLACEHOLDER  # 没有参照，坐标不动
        assert p3.provenance["location"]["original"] == list(PLACEHOLDER)
        assert (report.relocated, report.unresolved) == (2, 1)
        assert catalog_basis(p3)[1] == "坐标为省级占位，位置不可靠，暂不估算"
        assert catalog_basis(p1)[1] is None

    def test_重复执行结果不变(self):
        plants = placeholder_case()
        quality.apply(plants)
        first = [(p.latitude, p.longitude, p.name_local, dict(p.provenance)) for p in plants]
        quality.apply(plants)
        assert [
            (p.latitude, p.longitude, p.name_local, dict(p.provenance)) for p in plants
        ] == first

    def test_同县近似坐标不算占位(self):
        plants = [gem(f"c{i}", 36.29, 115.24, city="Handan", district="Daming") for i in range(4)]
        quality.apply(plants)
        assert all("location" not in p.provenance for p in plants)


class TestWri:
    def test_被GEM覆盖的标重复_其余借最近GEM补省市(self):
        g = gem("g1", 40.00, 95.00, city="Jiuquan", province="甘肃省", capacity=50_000)
        g_solar = gem("g2", 40.30, 95.00, kind="solar", city="Jiuquan", province="甘肃省")
        dup = wri("w1", 40.05, 95.00)  # 约 5.6 km，同类型 50 MW ≥ 49 MW
        alone = wri("w2", 40.25, 95.00)  # 20 km 内没有同类型容量，最近是 5.6 km 外的光伏
        plants = [g, g_solar, dup, alone]
        report = quality.apply(plants)
        assert dup.status == quality.DUPLICATE and dup.provenance["duplicate"]["nearest"] == ["g1"]
        assert alone.status == "operating"
        assert (alone.province, alone.city) == ("甘肃省", "Jiuquan")
        assert alone.provenance["region_from"]["id"] == "g2"
        assert (report.duplicates, report.regions) == (1, 1)

    def test_大型汇总条目按50公里判定(self):
        farms = [gem(f"g{i}", 40.0 + i * 0.1, 95.0, capacity=2_000_000) for i in range(4)]
        base = wri("gansu", 40.25, 95.3, capacity=6_000_000)
        quality.apply([*farms, base])
        assert base.status == quality.DUPLICATE


class TestNames:
    def test_合并场址用最大一期中文名(self):
        phases = [
            {"id": "G1", "capacity_kw": 20_000, "name": "枞阳县汤沟镇一期光伏"},
            {"id": "G2", "capacity_kw": 260_000, "name": "枞阳县汤沟镇260兆瓦光伏复合发电项目"},
        ]
        p = gem("m", 30.7, 117.2, kind="solar", local="Congyang光伏场址（2期合计）", phases=phases)
        quality.apply([p])
        assert p.name_local == "枞阳县汤沟镇260兆瓦光伏复合发电项目等2期"
        assert p.provenance["name_local_source"] == "Congyang光伏场址（2期合计）"

    @pytest.mark.parametrize(
        "names,capacities,expected",
        [
            (
                ["Anji Power I solar project", "Anji Power III solar project"],
                [1_800, 1_800],
                ["I", "III"],
            ),
            (
                ["Anji Power solar project", "Anji Power solar project"],
                [1_800, 6_000],
                ["1.8 MW", "6 MW"],
            ),
            (["Anji Power solar project", "Anji Power solar project"], [1_800, 1_800], ["1", "2"]),
        ],
    )
    def test_同名不同项目加后缀(self, names, capacities, expected):
        plants = [
            gem(
                f"s{i}",
                30.6 + i,
                119.7,
                kind="solar",
                local="安吉晶能光伏电力有限公司",
                name=n,
                capacity=c,
            )
            for i, (n, c) in enumerate(zip(names, capacities, strict=True))
        ]
        quality.apply(plants)
        assert [p.name_local for p in plants] == [
            f"安吉晶能光伏电力有限公司（{s}）" for s in expected
        ]


def test_汇总不把同名同坐标同容量的不同分期当重复():
    plants = [
        gem(
            f"b{i}",
            38.858,
            115.4907,
            kind="solar",
            local="白沟商业屋顶分布式光伏发电项目",
            capacity=1_600,
        )
        for i in range(5)
    ]
    rows, duplicate, invalid = eligible(plants)
    assert (len(rows), duplicate, invalid) == (5, 0, 0)
