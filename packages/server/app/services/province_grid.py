"""限电第二层：省级月度新能源利用率参考。docs/17 §四、docs/07 §2.6

全国新能源消纳监测预警中心按月公布各省风电、光伏利用率（只计系统原因受限的电量），整理在
同目录的 province_utilization.json，按月手工更新。月均利用率与逐日逐时的真实限电差距很大，只作
参考口径：不改可发电量，不改环境指数，不写入逐日累积与 AI 输入；只用于公开电站与全目录汇总，
自建场站由用户自己填出力约束（第一层）。界面默认不显示，由用户打开。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).with_name("province_utilization.json")

# 目录省份全称 → 利用率统计区域。港澳台不在统计范围
PROVINCE_REGION = {
    "北京市": "北京",
    "天津市": "天津",
    "河北省": "河北",
    "山西省": "山西",
    "辽宁省": "辽宁",
    "吉林省": "吉林",
    "黑龙江省": "黑龙江",
    "上海市": "上海",
    "江苏省": "江苏",
    "浙江省": "浙江",
    "安徽省": "安徽",
    "福建省": "福建",
    "江西省": "江西",
    "山东省": "山东",
    "河南省": "河南",
    "湖北省": "湖北",
    "湖南省": "湖南",
    "广东省": "广东",
    "广西壮族自治区": "广西",
    "海南省": "海南",
    "重庆市": "重庆",
    "四川省": "四川",
    "贵州省": "贵州",
    "云南省": "云南",
    "西藏自治区": "西藏",
    "陕西省": "陕西",
    "甘肃省": "甘肃",
    "青海省": "青海",
    "宁夏回族自治区": "宁夏",
    "新疆维吾尔自治区": "新疆",
}
# 内蒙古按电网分区统计：蒙东为国网蒙东电力的呼伦贝尔、兴安、通辽、赤峰，其余盟市属蒙西电网。
# GEM 的市名是英文，中文名一并列上
MENGDONG = {
    "hulunbuir", "hinggan league", "hinggan", "xing'an league",
    "tongliao", "chifeng",
    "呼伦贝尔市", "兴安盟", "通辽市", "赤峰市",
}  # fmt: skip
MENGXI = {
    "hohhot", "hohhot municipality", "baotou", "wuhai", "ordos", "bayannur",
    "ulanqab", "xilingol", "xilingol league", "alxa", "alxa league",
    "呼和浩特市", "包头市", "乌海市", "鄂尔多斯市",
    "巴彦淖尔市", "乌兰察布市", "锡林郭勒盟", "阿拉善盟",
}  # fmt: skip


@dataclass(frozen=True)
class Utilization:
    region: str
    period: str  # YYYY-MM 为当月值，YYYY 为全年值
    value: float  # 0–1
    source: str


def region_of(province: str | None, city: str | None) -> str | None:
    """目录省份（与城市）→ 统计区域。内蒙古城市不详时无法分区，不猜，返回 None。"""
    if not province:
        return None
    if province == "内蒙古自治区":
        c = (city or "").strip().lower()
        return "蒙东" if c in MENGDONG else "蒙西" if c in MENGXI else None
    return PROVINCE_REGION.get(province)


@lru_cache(maxsize=1)
def _load() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"periods": []}


@lru_cache(maxsize=1)
def digest() -> str:
    """数据文件指纹，进计算参数：每月更新数据后全目录快照与曲线缓存随之失效。"""
    try:
        return hashlib.sha256(DATA_FILE.read_bytes()).hexdigest()[:12]
    except OSError:
        return "missing"


def source() -> str:
    return str(_load().get("source") or "")


@lru_cache(maxsize=4096)
def lookup(region: str | None, kind: str, day: date) -> Utilization | None:
    """按优先级取目标日可用的利用率：

    1. 目标年当月的当月值
    2. 往年同一月份的当月值（取最近一年）—— 限电季节性很强，春季大风、汛期水电挤占，
       发布又滞后 5–6 周，拿冬季月份给九月折减会系统性偏差
    3. 目标月以前最近一期的当月值
    4. 目标年及以前最近的全年值

    累计值不用。响应里带统计期，不冒充当月。
    """
    if region is None or kind not in ("wind", "solar"):
        return None
    data = _load()
    month = f"{day.year:04d}-{day.month:02d}"
    best: tuple[tuple[int, str], str, float] | None = None
    for release in data.get("periods", []):
        value = ((release.get("rates") or {}).get(region) or {}).get(kind)
        if not isinstance(value, int | float) or not 0 < value <= 1:
            continue
        period, kind_of = str(release.get("period")), release.get("kind")
        if kind_of == "month" and period <= month:
            same_month = period[5:7] == month[5:7]
            key = (3 if period == month else 2 if same_month else 1, period)
        elif kind_of == "year" and period <= str(day.year):
            key = (0, period)
        else:
            continue
        if best is None or key > best[0]:
            best = (key, period, float(value))
    if best is None:
        return None
    return Utilization(region, best[1], best[2], source())


def reset() -> None:
    """数据文件替换后清缓存（测试与热更新用）。"""
    _load.cache_clear()
    digest.cache_clear()
    lookup.cache_clear()


def station_reference(energy_kwh: float | None, region: str | None, kind: str, day: date):
    """单站：可发电量 × 利用率。没有电量或没有该区域数据时返回 None。"""
    from app.schemas.prediction import ProvinceGrid

    util = lookup(region, kind, day)
    if energy_kwh is None or util is None:
        return None
    grid = round(energy_kwh * util.value, 2)
    return ProvinceGrid(
        region=util.region,
        period=util.period,
        utilization=util.value,
        energy_kwh=grid,
        curtailed_kwh=round(energy_kwh - grid, 2),
        source=util.source,
    )


class FleetAccumulator:
    """全目录逐日累加：有省级数据的电站按利用率折算，没有的按可发电量计入并单独计数。"""

    def __init__(self, days: list[date]) -> None:
        self.days = days
        self.grid = [0.0] * len(days)
        self.potential = [0.0] * len(days)
        self.applied = [0] * len(days)
        self.unapplied = [0] * len(days)
        self.periods: list[set[str]] = [set() for _ in days]

    def add(self, k: int, kind: str, province: str | None, city: str | None, energy_kwh: float):
        util = lookup(region_of(province, city), kind, self.days[k])
        self.potential[k] += energy_kwh
        if util is None:
            self.grid[k] += energy_kwh
            self.unapplied[k] += 1
            return
        self.grid[k] += energy_kwh * util.value
        self.applied[k] += 1
        self.periods[k].add(util.period)

    def result(self, k: int):
        from app.schemas.prediction import FleetProvinceGrid

        if not self.applied[k]:
            return None
        return FleetProvinceGrid(
            energy_kwh=round(self.grid[k], 2),
            curtailed_kwh=round(self.potential[k] - self.grid[k], 2),
            applied_count=self.applied[k],
            unapplied_count=self.unapplied[k],
            periods=sorted(self.periods[k]),
            source=source(),
        )
