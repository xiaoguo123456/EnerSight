"""目录容量口径：未知类型不猜测；已知交流/直流采用明确的设备假设。"""

import math

DC_AC_RATIO = 1.2
INVERTER_EFFICIENCY = 0.96
VERSION = "capacity-v2"


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
