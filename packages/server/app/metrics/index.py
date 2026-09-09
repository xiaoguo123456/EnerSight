"""新能源环境指数。docs/07 §一

    指数 = 今日预测发电量 / 今日理想发电量 × 100

不是四因子加权求和 —— 加权方案有共线性（云量与辐射重复计入）、
补偿性（夜间辐射为 0 仍能靠温度风速得分）和权重无依据三个结构缺陷。
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.config import settings
from app.metrics import pv, solar, wind
from app.metrics.pv import PvInputs
from app.schemas.common import IndexAttribution, IndexAttributionFactor, IndexLevel

__all__ = ["IndexResult", "PvInputs", "classify", "pv_index", "wind_index"]


@dataclass(frozen=True)
class IndexResult:
    score: float
    level: IndexLevel
    actual_kwh: float
    ideal_kwh: float
    attribution: list[IndexAttribution]
    # 今日逐时出力（kW），与 actual_kwh 同一条链路算出，供当前功率与预测曲线复用
    hourly_kw: pd.Series | None = field(default=None, compare=False, repr=False)


def classify(score: float) -> IndexLevel:
    if score >= settings.index_excellent:
        return IndexLevel.EXCELLENT
    if score >= settings.index_good:
        return IndexLevel.GOOD
    if score >= settings.index_fair:
        return IndexLevel.FAIR
    return IndexLevel.POOR


def _check_complete(inp: PvInputs) -> None:
    for name in ("ghi", "dni", "dhi", "temp_air", "wind_speed"):
        s = getattr(inp, name)
        if not np.isfinite(s.to_numpy(dtype=float)).all():
            raise ValueError(f"pv_index 输入 {name} 含缺测；缺测判定与填补由 services.energy 负责")


def pv_index(inp: PvInputs) -> IndexResult:
    """光伏指数。分子分母跑同一个模型链，只换输入。输入须完整（无 NaN）。"""
    _check_complete(inp)
    # 理想基准与 Open-Meteo 同口径：前一小时均值，不是整点瞬时值
    cs = solar.clearsky_hourly_mean(inp.latitude, inp.longitude, inp.tz, inp.times)
    ideal_temp = pd.Series(pv.IDEAL_TEMP_AIR, index=inp.times)
    ideal_wind = pd.Series(pv.IDEAL_WIND_SPEED, index=inp.times)

    actual_kw = pv.hourly_power(inp)
    actual = pv.daily_energy_kwh(actual_kw)
    ideal = pv.daily_energy_kwh(
        pv.hourly_power(
            inp,
            ghi=cs["ghi"],
            dni=cs["dni"],
            dhi=cs["dhi"],
            temp_air=ideal_temp,
            wind_speed=ideal_wind,
        )
    )

    score = 0.0 if ideal <= 0 else min(100.0, actual / ideal * 100.0)

    # 归因：逐项把因子换成理想值重算，差值即该因子的贡献。docs/07 §1.6
    # 算出来的，不是定义的权重。
    attribution: list[IndexAttribution] = []
    if ideal > 0:

        def pct(v: float) -> float:
            return v / ideal * 100.0

        no_cloud = pv.daily_energy_kwh(
            pv.hourly_power(inp, ghi=cs["ghi"], dni=cs["dni"], dhi=cs["dhi"])
        )
        kt = actual / no_cloud if no_cloud > 0 else 0.0
        attribution.append(
            IndexAttribution(
                factor=IndexAttributionFactor.RADIATION,
                delta=round(pct(actual) - pct(no_cloud), 1),
                description=f"云层使辐照降至晴空的 {kt * 100:.0f}%",
            )
        )

        ideal_temp_only = pv.daily_energy_kwh(pv.hourly_power(inp, temp_air=ideal_temp))
        t_avg = float(inp.temp_air.mean())
        attribution.append(
            IndexAttribution(
                factor=IndexAttributionFactor.TEMPERATURE,
                delta=round(pct(actual) - pct(ideal_temp_only), 1),
                description=f"平均气温 {t_avg:.0f}℃ 对组件效率的影响",
            )
        )

        ideal_wind_only = pv.daily_energy_kwh(pv.hourly_power(inp, wind_speed=ideal_wind))
        v_avg = float(inp.wind_speed.mean())
        attribution.append(
            IndexAttribution(
                factor=IndexAttributionFactor.WIND,
                delta=round(pct(actual) - pct(ideal_wind_only), 1),
                description=f"平均风速 {v_avg:.1f} m/s 对组件散热的影响",
            )
        )

    return IndexResult(
        score=round(score, 1),
        level=classify(score),
        actual_kwh=actual,
        ideal_kwh=ideal,
        attribution=attribution,
        hourly_kw=actual_kw,
    )


# 风电容量因子 → 指数的分段线性映射。docs/07 §1.4
# 风电没有「晴空」这样的物理上界，拿满发做分母会让所有站长期低分，
# 因此改用容量因子。断点按 5 个风电基地一年的日 CF 分布校准（docs/07 §八）：
# 日 CF 是双峰的（静风日 / 大风日），0.22 ≈ 国内陆上风电年均 CF，落在「良」。
_CF_POINTS = [0.0, 0.10, 0.22, 0.38, 0.55]
_CF_SCORES = [0.0, 55.0, 70.0, 90.0, 100.0]


def wind_index(daily_kwh: float, capacity_kw: float) -> IndexResult:
    cf = wind.capacity_factor(daily_kwh, capacity_kw)
    # np.interp 在区间外自动钳制到端点值
    score = float(np.interp(cf, _CF_POINTS, _CF_SCORES))
    return IndexResult(
        score=round(score, 1),
        level=classify(score),
        actual_kwh=daily_kwh,
        ideal_kwh=capacity_kw * 24.0,
        attribution=[],
    )
