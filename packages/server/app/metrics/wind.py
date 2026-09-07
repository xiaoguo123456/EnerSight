"""风电出力模型。docs/07 §2.2

风电没有 pvlib 级别的标准库，用通用功率曲线。
"""

import numpy as np
import pandas as pd

from app.config import settings


def default_hub_height(capacity_kw: float) -> float:
    """轮毂高度默认值，按容量估算。docs/07 §2.3"""
    if capacity_kw <= 1500:
        return 70.0
    if capacity_kw <= 3000:
        return 85.0
    return 100.0


def extrapolate_wind(v10: pd.Series, hub_height: float) -> pd.Series:
    """10m 风速按幂律外推到轮毂高度。"""
    return v10 * (hub_height / 10.0) ** settings.wind_shear_alpha


def power_curve(v_hub: pd.Series, capacity_kw: float) -> pd.Series:
    """通用功率曲线。切入以下与切出以上出力为 0 —— 这是物理事实，
    不能被其他因素补偿，这也是环境指数不用加权求和的原因之一。
    """
    v_in, v_r, v_out = settings.wind_v_in, settings.wind_v_rated, settings.wind_v_out
    v = v_hub.to_numpy(dtype=float)

    ramp = capacity_kw * (v**3 - v_in**3) / (v_r**3 - v_in**3)
    p = np.select(
        [v < v_in, v < v_r, v <= v_out],
        [0.0, ramp, capacity_kw],
        default=0.0,
    )
    return pd.Series(p, index=v_hub.index).clip(lower=0, upper=capacity_kw)


def capacity_factor(daily_kwh: float, capacity_kw: float) -> float:
    """日容量因子。风电环境指数的输入。docs/07 §1.4"""
    if capacity_kw <= 0:
        return 0.0
    return daily_kwh / (capacity_kw * 24.0)
