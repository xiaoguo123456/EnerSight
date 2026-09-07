"""光伏出力模型。docs/07 §2.1

用 pvlib 标准链路 + PVWatts —— PVWatts 只需装机容量与温度系数，
不需要组件型号，正好匹配「用户只填装机容量」的输入约束。
"""

import pandas as pd
import pvlib

from app.config import settings

_TEMP_PARAMS = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]

# 理想条件下的参考值，用于算环境指数的分母。docs/07 §1.3
IDEAL_TEMP_AIR = 25.0
IDEAL_WIND_SPEED = 1.0


def default_tilt(latitude: float) -> float:
    """倾角默认取纬度绝对值 —— 最大化年发电量的经验法则。docs/07 §2.3"""
    return abs(latitude)


def dc_power(
    *,
    poa_global: pd.Series,
    temp_air: pd.Series,
    wind_speed: pd.Series,
    capacity_kw: float,
) -> pd.Series:
    """逐时直流出力（kW）。风速经电池温度模型影响散热，是物理建模不是加权项。"""
    params = _TEMP_PARAMS[settings.pv_temperature_model]
    temp_cell = pvlib.temperature.sapm_cell(
        poa_global=poa_global,
        temp_air=temp_air,
        wind_speed=wind_speed,
        a=params["a"],
        b=params["b"],
        deltaT=params["deltaT"],
    )
    dc = pvlib.pvsystem.pvwatts_dc(
        effective_irradiance=poa_global,
        temp_cell=temp_cell,
        pdc0=capacity_kw,
        gamma_pdc=settings.pv_gamma_pdc,
    )
    return dc.clip(lower=0) * (1 - settings.pv_losses)


def poa_from_components(
    *,
    tilt: float,
    azimuth: float,
    solar_zenith: pd.Series,
    solar_azimuth: pd.Series,
    dni: pd.Series,
    ghi: pd.Series,
    dhi: pd.Series,
) -> pd.Series:
    """倾斜面总辐照。"""
    total = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=solar_zenith,
        solar_azimuth=solar_azimuth,
        dni=dni,
        ghi=ghi,
        dhi=dhi,
    )
    return total["poa_global"].fillna(0.0)


def daily_energy_kwh(dc_kw: pd.Series) -> float:
    """逐时功率求和为日发电量。假定采样间隔 1 小时。"""
    return float(dc_kw.sum())
