"""目录容量口径：未知类型不猜测；已知交流/直流采用明确的设备假设。

容配比与出力模型共用 settings.pv_dc_ac_ratio；逆变器损耗计在 14% 系统损耗里（与 PVGIS
同口径，docs/07 §2.1），不再单独乘效率。VERSION 标记计算口径，模型改动要升版本，
留档与全目录历史按版本隔离。
"""

import math

from app.config import settings

DC_AC_RATIO = settings.pv_dc_ac_ratio
# capacity-v2：区分分期交直流容量；model-v3：太阳位置取区间中点、风电各层插值 + 场站损耗、
# 逆变器损耗并入系统损耗
VERSION = "model-v3"


def catalog_basis(plant):
    provenance = plant.provenance or {}
    phases = provenance.get("phases", [])
    if not phases:
        return None, "原始分期与容量来源尚未核验"
    dc = ac = 0.0
    for phase in phases:
        capacity = phase.get("capacity_kw", 0)
        if not isinstance(capacity, (float, int)) or not math.isfinite(capacity) or capacity <= 0:
            return None, "分期容量不完整"
        if plant.type == "wind":
            ac += capacity
            continue
        if phase.get("technology") and phase["technology"].lower() not in (
            "pv",
            "assumed pv",
            "photovoltaic",
        ):
            return None, "非光伏技术暂不适用当前发电模型"
        rating = phase.get("capacity_rating")
        if rating == "ac":
            ac += capacity
            dc += capacity * DC_AC_RATIO
        elif rating == "dc":
            dc += capacity
            ac += capacity / DC_AC_RATIO
        else:
            return None, "光伏交流/直流容量类型未知，暂不估算"
    return (dc, ac), None
