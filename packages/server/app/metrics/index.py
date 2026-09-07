"""新能源环境指数。docs/07 §一

    指数 = 今日预测发电量 / 今日理想发电量 × 100

不是四因子加权求和 —— 加权方案有共线性（云量与辐射重复计入）、
补偿性（夜间辐射为 0 仍能靠温度风速得分）和权重无依据三个结构缺陷。
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.config import settings
from app.metrics import pv, solar, wind
from app.schemas.common import IndexAttribution, IndexAttributionFactor, IndexLevel


@dataclass(frozen=True)
class PvInputs:
    latitude: float
    longitude: float
    tz: str
    capacity_kw: float
    tilt: float
    azimuth: float
    times: pd.DatetimeIndex
    ghi: pd.Series
    dni: pd.Series
    dhi: pd.Series
    temp_air: pd.Series
    wind_speed: pd.Series


@dataclass(frozen=True)
class IndexResult:
    score: float
    level: IndexLevel
    actual_kwh: float
    ideal_kwh: float
    attribution: list[IndexAttribution]


def classify(score: float) -> IndexLevel:
    if score >= settings.index_excellent:
        return IndexLevel.EXCELLENT
    if score >= settings.index_good:
        return IndexLevel.GOOD
    if score >= settings.index_fair:
        return IndexLevel.FAIR
    return IndexLevel.POOR


def _pv_energy(
    inp: PvInputs,
    *,
    ghi: pd.Series,
    dni: pd.Series,
    dhi: pd.Series,
    temp_air: pd.Series,
    wind_speed: pd.Series,
) -> float:
    pos = solar.solar_position(inp.latitude, inp.longitude, inp.tz, inp.times)
    poa = pv.poa_from_components(
        tilt=inp.tilt,
        azimuth=inp.azimuth,
        solar_zenith=pos["apparent_zenith"],
        solar_azimuth=pos["azimuth"],
        dni=dni,
        ghi=ghi,
        dhi=dhi,
    )
    dc = pv.dc_power(
        poa_global=poa,
        temp_air=temp_air,
        wind_speed=wind_speed,
        capacity_kw=inp.capacity_kw,
    )
    return pv.daily_energy_kwh(dc)


def pv_index(inp: PvInputs) -> IndexResult:
    """光伏指数。分子分母跑同一个模型链，只换输入。"""
    cs = solar.clearsky(inp.latitude, inp.longitude, inp.tz, inp.times)
    ideal_temp = pd.Series(pv.IDEAL_TEMP_AIR, index=inp.times)
    ideal_wind = pd.Series(pv.IDEAL_WIND_SPEED, index=inp.times)

    actual = _pv_energy(
        inp,
        ghi=inp.ghi,
        dni=inp.dni,
        dhi=inp.dhi,
        temp_air=inp.temp_air,
        wind_speed=inp.wind_speed,
    )
    ideal = _pv_energy(
        inp,
        ghi=cs["ghi"],
        dni=cs["dni"],
        dhi=cs["dhi"],
        temp_air=ideal_temp,
        wind_speed=ideal_wind,
    )

    score = 0.0 if ideal <= 0 else min(100.0, actual / ideal * 100.0)

    # 归因：逐项把因子换成理想值重算，差值即该因子的贡献。docs/07 §1.6
    # 算出来的，不是定义的权重。
    attribution: list[IndexAttribution] = []
    if ideal > 0:

        def pct(v: float) -> float:
            return v / ideal * 100.0

        no_cloud = _pv_energy(
            inp,
            ghi=cs["ghi"],
            dni=cs["dni"],
            dhi=cs["dhi"],
            temp_air=inp.temp_air,
            wind_speed=inp.wind_speed,
        )
        kt = actual / no_cloud if no_cloud > 0 else 0.0
        attribution.append(
            IndexAttribution(
                factor=IndexAttributionFactor.RADIATION,
                delta=round(pct(actual) - pct(no_cloud), 1),
                description=f"云层使辐照降至晴空的 {kt * 100:.0f}%",
            )
        )

        ideal_temp_only = _pv_energy(
            inp,
            ghi=inp.ghi,
            dni=inp.dni,
            dhi=inp.dhi,
            temp_air=ideal_temp,
            wind_speed=inp.wind_speed,
        )
        t_avg = float(inp.temp_air.mean())
        attribution.append(
            IndexAttribution(
                factor=IndexAttributionFactor.TEMPERATURE,
                delta=round(pct(actual) - pct(ideal_temp_only), 1),
                description=f"平均气温 {t_avg:.0f}℃ 对组件效率的影响",
            )
        )

    return IndexResult(
        score=round(score, 1),
        level=classify(score),
        actual_kwh=actual,
        ideal_kwh=ideal,
        attribution=attribution,
    )


# 风电容量因子 → 指数的分段线性映射。docs/07 §1.4
# 风电没有「晴空」这样的物理上界，拿满发做分母会让所有站长期低分，
# 因此改用容量因子。断点取自陆上风电 CF 的典型分布，可配置。
_CF_POINTS = [0.0, 0.18, 0.30, 0.45, 0.60]
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
