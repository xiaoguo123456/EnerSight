"""目录容量口径：未知类型不猜测；已知交流/直流采用明确的设备假设。

容配比与出力模型共用 settings.pv_dc_ac_ratio；逆变器损耗计在 14% 系统损耗里（与 PVGIS
同口径，docs/07 §2.1），不再单独乘效率。VERSION 标记计算口径，模型改动要升版本，
留档与全目录历史按版本隔离。
"""

import hashlib
import json
import math
from datetime import date

from app.config import settings
from app.services.catalog import is_offshore

DC_AC_RATIO = settings.pv_dc_ac_ratio
# capacity-v2：区分分期交直流容量；model-v3：太阳位置取区间中点、风电各层插值 + 场站损耗、
# 逆变器损耗并入系统损耗
VERSION = "model-v4"


def calculation_parameters() -> dict:
    """仅保留计算设置，供缓存指纹和离线复现使用。"""
    from app.services import province_grid

    parameters = {
        k: v
        for k, v in settings.model_dump(mode="json").items()
        if k.startswith(("pv_", "wind_", "fleet_grid_", "outlook_", "index_"))
        or k in ("forecast_outlook_days", "model_v4_start_date")
    }
    # 省级利用率按月更新，更新后全目录快照与曲线缓存要失效。docs/17 §四
    parameters["province_utilization"] = province_grid.digest()
    return parameters


def calculation_version(day: date | str) -> str:
    """时间口径与算法修订分开；参数变化必须使缓存和留档指纹变化。"""
    parameters = calculation_parameters()
    digest = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()[:12]
    # 修订4：目录光伏未声明交直流口径时按直流解释（原先拒绝估算）
    return f"{version_for_day(day)}-修订4-{digest}"


def station_parameters(station) -> dict:
    """复现预测所需的设备、容量及约束快照，不包含用户身份。"""
    keys = (
        "type",
        "latitude",
        "longitude",
        "capacity_kw",
        "tilt",
        "azimuth",
        "hub_height",
        "turbine_class",
        "power_curve",
        "mounting",
        "bifacial",
        "curtailment",
    )
    return {
        **{k: getattr(station, k, None) for k in keys},
        "capacity_basis": getattr(station, "_pv_capacity", None),
        "blocked_reason": getattr(station, "_prediction_blocked", None),
    }


def version_for_day(day: date | str) -> str:
    """按预测目标日切换版本，不在同一天中途更改日累计定义。"""
    target = date.fromisoformat(day) if isinstance(day, str) else day
    return VERSION if target >= settings.model_v4_start_date else "model-v3"


def catalog_basis(plant):
    """目录站点的 (直流, 交流) 容量、不可估算原因、以及是否用了直流假设。

    返回三元组；`[0]`/`[1]` 的既有取法不变。第三项为 True 表示这座站里至少有一个
    分期没有声明交直流口径，按**直流**解释 —— 是假设，必须在界面上标出来。
    """
    provenance = plant.provenance or {}
    location = provenance.get("location") or {}
    if location.get("tier") == "province" and not location.get("method"):
        # 省级占位且同县、同市都没有可靠参照（app/catalog/quality.py），气象会差几百公里
        return None, "坐标为省级占位，位置不可靠，暂不估算", False
    phases = provenance.get("phases", [])
    if not phases:
        return None, "原始分期与容量来源尚未核验", False
    dc = ac = 0.0
    assumed = False
    for phase in phases:
        capacity = phase.get("capacity_kw", 0)
        if not isinstance(capacity, (float, int)) or not math.isfinite(capacity) or capacity <= 0:
            return None, "分期容量不完整", False
        if plant.type == "wind":
            ac += capacity
            continue
        if phase.get("technology") and phase["technology"].lower() not in (
            "pv",
            "assumed pv",
            "photovoltaic",
        ):
            return None, "非光伏技术暂不适用当前发电模型", False
        rating = phase.get("capacity_rating")
        if rating == "ac":
            ac += capacity
            dc += capacity * DC_AC_RATIO
        else:
            # rating == "dc"，或未声明按直流处理。GEM 那一列多半是空的：生产库实测
            # 9,822 个光伏分期 unknown（占七成），而它写明口径的 3,796 个里 71% 是 dc；
            # 按直流解释出力也更保守（ac = cap / 容配比）。原先这里直接拒绝估算，
            # 挡掉了 13,472 座光伏里的 9,493 座。口径与实测数据见 docs/07 §2.1
            dc += capacity
            ac += capacity / DC_AC_RATIO
            assumed = assumed or rating != "dc"
    return (dc, ac), None, assumed


def wind_turbine_class(commissioning_year: int | None, offshore: bool) -> str | None:
    """目录风电的默认机型档，None 即通用功率曲线。docs/07 §2.2

    REIT 场站电量对账（docs/07 §8.1 2026-09-15）：2016–2020 年投运的陆上机型比功率
    175–260 W/m²，通用曲线少算 26–103%，换低风速档后 87–109%；2011 年投运、370 W/m² 的
    老机型通用曲线持平。两座海上站换档没有一致改善（滨海北中风速档 96%、庄河Ⅲ 82%），
    保留通用曲线。年份未知多为新近并网，按新站处理。
    """
    if offshore:
        return None
    if (
        commissioning_year is not None
        and commissioning_year < settings.wind_catalog_modern_from_year
    ):
        return None
    return settings.wind_catalog_modern_class


def _representative_year(plant) -> int | None:
    """多期场址按容量加权取中位投运年份；分期缺年份（导入该字段前的旧库）时退回首期年份。"""
    phases = (plant.provenance or {}).get("phases") or []
    rows = [(ph.get("start_year"), ph.get("capacity_kw") or 0) for ph in phases]
    if not rows or any(year is None for year, _ in rows):
        return plant.commissioning_year
    half, total = sum(c for _, c in rows) / 2, 0.0
    for year, capacity in sorted(rows):
        total += capacity
        if total >= half:
            return year
    return plant.commissioning_year


def catalog_turbine_class(plant) -> str | None:
    if plant.type != "wind":
        return None
    return wind_turbine_class(_representative_year(plant), is_offshore(plant))


def catalog_cell_selection(plant) -> str | None:
    """海上风电取海上格点：上游默认 land 会把近岸场站分到陆地格点，10 m 风速偏低三成。"""
    return "sea" if is_offshore(plant) else None
