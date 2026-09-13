"""风电出力模型。docs/07 §2.2

风电没有 pvlib 级别的标准库，用通用功率曲线。

轮毂高度风速：Open-Meteo 直接给 10 / 80 / 100 / 120 m 各层风速，按对数风廓线在
相邻两层之间插值到轮毂高度。风切变随大气稳定度昼夜变化近 3 倍（白天 α≈0.1、
夜间 ≈0.3），用固定指数从 10 m 外推会把夜间风电系统性算低，只在缺高层数据时降级使用。
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.config import settings

# Open-Meteo 提供的风速高度层 → 列名。docs/04 §二
LEVEL_COLUMNS: dict[float, str] = {
    10.0: "wind_speed_10m",
    80.0: "wind_speed_80m",
    100.0: "wind_speed_100m",
    120.0: "wind_speed_120m",
}


@dataclass(frozen=True)
class Turbine:
    """机型：档位或自定义曲线。curve 为 (轮毂风速 m/s, 出力 / 额定 0–1) 升序点列。"""

    cls: str = "generic"
    curve: tuple[tuple[float, float], ...] | None = None


# 机型档：(切入, 额定, 切出) m/s。通用档取 settings；低 / 中 / 高风速按比功率区分。docs/07 §2.2
TURBINE_CLASSES: dict[str, tuple[float, float, float]] = {
    "low_wind": (2.5, 9.5, 22.0),
    "medium_wind": (3.0, 11.0, 25.0),
    "high_wind": (3.5, 12.5, 25.0),
}
TURBINE_LABELS = {
    "generic": "通用功率曲线",
    "low_wind": "低风速机型（额定 9.5 m/s）",
    "medium_wind": "中风速机型（额定 11 m/s）",
    "high_wind": "高风速机型（额定 12.5 m/s）",
    "custom": "自定义功率曲线",
}


def turbine_for(station) -> Turbine:
    """站点字段 → Turbine。自定义档没有曲线时退回通用档。"""
    cls = getattr(station, "turbine_class", None) or "generic"
    raw = getattr(station, "power_curve", None)
    if cls == "custom" and raw:
        curve = tuple(sorted((float(p["v"]), float(p["p"]) / 100.0) for p in raw))
        return Turbine("custom", curve)
    return Turbine(cls if cls in TURBINE_CLASSES else "generic", None)


def air_density(
    pressure_hpa: pd.Series | None, temp_c: pd.Series, elevation: float | None
) -> pd.Series:
    """轮毂高度空气密度 ρ = p / (R T)。

    有气压用气压；缺气压按 ISA 由海拔换算；连海拔都没有按标称 1.225。
    3000 m 海拔 ρ 约 0.9，不修正会把额定以下的出力系统性高估两到三成。
    """
    t_k = temp_c.astype(float) + 273.15
    p = (
        pressure_hpa.astype(float) * 100.0
        if pressure_hpa is not None
        else pd.Series(np.nan, index=temp_c.index)
    )
    if elevation is not None:
        p = p.fillna(101325.0 * (1.0 - 2.25577e-5 * elevation) ** 5.25588)
    return (p / (287.05 * t_k)).fillna(settings.wind_air_density_ref)


def density_corrected_speed(v_hub: pd.Series, rho: pd.Series) -> pd.Series:
    """IEC 61400-12-1 变桨机组密度修正：v' = v (ρ / ρ0)^(1/3)，再查标称曲线。"""
    return v_hub * (rho / settings.wind_air_density_ref) ** (1.0 / 3.0)


def default_hub_height() -> float:
    """未填轮毂高度时的默认值。docs/07 §2.3

    早先按容量分档（≤1.5 MW 70 m …）针对的是单机容量，而站点容量是全场容量，
    目录里所有场站都会落到最高档，分档没有意义，改为单一默认值。
    """
    return settings.wind_hub_height_default


def extrapolate_wind(v10: pd.Series, hub_height: float) -> pd.Series:
    """10 m 风速按幂律外推到轮毂高度。降级路径：只有 10 m 数据时用。"""
    return v10 * (hub_height / 10.0) ** settings.wind_shear_alpha


def hub_wind_speed(
    levels: dict[float, pd.Series], hub_height: float
) -> tuple[pd.Series, pd.Series]:
    """多层风速 → 轮毂高度风速。

    每个时刻按当时有效的高度层：
    - 轮毂在两层之间：对数廓线（风速对 ln z 线性）插值
    - 轮毂高于最高层：用最高两层的对数廓线斜率外推，不低于 0
    - 轮毂低于最低有效层：用最低两层的斜率向下外推，不低于 0
    - 只有一层有效：从该层按幂律外推（`wind_shear_alpha`），标记为降级
    - 一层都没有：NaN

    返回 (v_hub, fallback)。fallback 为 True 的时刻走了幂律降级。
    """
    heights = np.array(sorted(levels), dtype=float)
    index = next(iter(levels.values())).index
    values = np.column_stack([levels[h].astype(float).reindex(index).to_numpy() for h in heights])
    ln_z = np.log(heights)
    ln_h = math.log(hub_height)

    out = np.full(len(index), np.nan)
    fallback = np.zeros(len(index), dtype=bool)
    for i, row in enumerate(values):
        ok = np.isfinite(row)
        n = int(ok.sum())
        if n == 0:
            continue
        z, v = ln_z[ok], row[ok]
        if n == 1:
            out[i] = v[0] * (hub_height / heights[ok][0]) ** settings.wind_shear_alpha
            fallback[i] = True
        elif ln_h < z[0]:
            # 轮毂低于最低有效层：按最低两层的对数廓线斜率向下外推。
            # np.interp 会钳制到最低层，把 50 m 轮毂直接当成 80 m 风速，系统性高估。
            slope = (v[1] - v[0]) / (z[1] - z[0])
            out[i] = max(0.0, v[0] + slope * (ln_h - z[0]))
            fallback[i] = True
        elif ln_h <= z[-1]:
            out[i] = float(np.interp(ln_h, z, v))
        else:
            slope = (v[-1] - v[-2]) / (z[-1] - z[-2])
            out[i] = max(0.0, v[-1] + slope * (ln_h - z[-1]))
    return pd.Series(out, index=index), pd.Series(fallback, index=index)


def power_curve(v_hub: pd.Series, capacity_kw: float, turbine: Turbine | None = None) -> pd.Series:
    """通用功率曲线（机组毛出力）。切入以下与切出以上出力为 0 —— 这是物理事实，
    不能被其他因素补偿，这也是环境指数不用加权求和的原因之一。

    NaN 风速 → NaN 出力，不当 0。
    """
    turbine = turbine or Turbine()
    v = v_hub.to_numpy(dtype=float)
    if turbine.curve:
        vs = np.array([c[0] for c in turbine.curve], dtype=float)
        ps = np.array([c[1] for c in turbine.curve], dtype=float) * capacity_kw
        # 首点之前为切入前、末点之后为切出，都是 0
        p = np.interp(v, vs, ps, left=0.0, right=0.0)
    else:
        v_in, v_r, v_out = TURBINE_CLASSES.get(
            turbine.cls, (settings.wind_v_in, settings.wind_v_rated, settings.wind_v_out)
        )
        ramp = capacity_kw * (v**3 - v_in**3) / (v_r**3 - v_in**3)
        p = np.select(
            [v < v_in, v < v_r, v <= v_out],
            [0.0, ramp, capacity_kw],
            default=0.0,
        )
    p = np.where(np.isfinite(v), p, np.nan)
    return pd.Series(p, index=v_hub.index).clip(lower=0, upper=capacity_kw)


def plant_power(
    v_hub: pd.Series,
    capacity_kw: float,
    *,
    rho: pd.Series | None = None,
    turbine: Turbine | None = None,
) -> pd.Series:
    """场站净出力：密度修正 → 功率曲线 × (1 − 尾流 / 可利用率 / 电气损耗)。"""
    turbine = turbine or Turbine()
    v_in, _, v_out = TURBINE_CLASSES.get(
        turbine.cls, (settings.wind_v_in, settings.wind_v_rated, settings.wind_v_out)
    )
    if turbine.curve:
        v_in, v_out = turbine.curve[0][0], turbine.curve[-1][0]
    v = density_corrected_speed(v_hub, rho) if rho is not None else v_hub
    # 密度只修正气动出力，不能移动真实风速的运行边界。
    # 高密度时等效风速可能越过切出点，运行区间内仍按曲线末端求出力。
    power = power_curve(v.clip(upper=v_out), capacity_kw, turbine)
    power = power.where((v_hub >= v_in) & (v_hub <= v_out), 0.0)
    return power.where(np.isfinite(v_hub), np.nan) * (1 - settings.wind_losses)


def capacity_factor(daily_kwh: float, capacity_kw: float) -> float:
    """日容量因子。风电环境指数的输入。docs/07 §1.4"""
    if capacity_kw <= 0:
        return 0.0
    return daily_kwh / (capacity_kw * 24.0)
