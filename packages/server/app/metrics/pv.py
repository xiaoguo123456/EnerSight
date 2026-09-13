"""光伏出力模型。docs/07 §2.1

用 pvlib 标准链路 + PVWatts —— PVWatts 只需装机容量与温度系数，
不需要组件型号，正好匹配「用户只填装机容量」的输入约束。

这是光伏逐时出力的唯一实现：环境指数、发电估算、当前功率、区域预测全部从
`hourly_power` 出，改一处处处都变。
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pvlib

from app.config import settings
from app.metrics import solar

_TEMP_PARAMS = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]

# 理想条件下的参考值，用于算环境指数的分母。docs/07 §1.3
IDEAL_TEMP_AIR = 25.0
IDEAL_WIND_SPEED = 1.0


@dataclass(frozen=True)
class PvInputs:
    """一天的等间隔输入。times 标注区间末（Open-Meteo 口径），步长由 step_minutes 指定，各列同索引。

    缺测保持 NaN，模型原样透传为 NaN，不当 0 —— 判定「不可算」是调用方的事。
    """

    latitude: float
    longitude: float
    tz: str
    capacity_kw: float  # 交流侧额定容量
    tilt: float
    azimuth: float
    times: pd.DatetimeIndex
    ghi: pd.Series
    dni: pd.Series
    dhi: pd.Series
    temp_air: pd.Series
    wind_speed: pd.Series
    dc_capacity_kw: float | None = None  # 直流侧已知时给出；否则按容配比换算
    step_minutes: int = 60  # 区间长度：逐小时 60，短期 96 点曲线 15
    mounting: str = "fixed"  # fixed | single_axis，见 docs/07 §2.1
    bifacial: bool = False


def default_tilt(latitude: float) -> float:
    """倾角默认取纬度绝对值 —— 最大化年发电量的经验法则。docs/07 §2.3"""
    return abs(latitude)


def ac_power(
    *,
    poa_global: pd.Series,
    temp_air: pd.Series,
    wind_speed: pd.Series,
    capacity_kw: float,
    dc_capacity_kw: float | None = None,
) -> pd.Series:
    """逐时交流出力（kW）。

    - 风速经电池温度模型影响散热，是物理建模不是加权项
    - 站点容量是交流侧（docs/04 §七），直流侧 pdc0 = 已知直流容量，否则容量 × 容配比
    - `pv_losses` 含逆变器损耗（与 PVGIS 的 14% 同口径），交流出力按额定容量限幅
    - NaN 输入 → NaN 输出
    """
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
        pdc0=dc_capacity_kw
        if dc_capacity_kw is not None
        else capacity_kw * settings.pv_dc_ac_ratio,
        gamma_pdc=settings.pv_gamma_pdc,
    )
    return (dc.clip(lower=0) * (1 - settings.pv_losses)).clip(upper=capacity_kw)


def poa_from_components(
    *,
    tilt: float | pd.Series,
    azimuth: float | pd.Series,
    solar_zenith: pd.Series,
    solar_azimuth: pd.Series,
    dni: pd.Series,
    ghi: pd.Series,
    dhi: pd.Series,
) -> pd.Series:
    """倾斜面总辐照。

    散射用 Perez 模型（pvlib 推荐、行业常用）。校准发现各向同性模型在晴朗干旱区
    系统性低估 3–5%（缺环日散射分量），换 Perez 后 5 个气候区年发电量全部落入
    PVGIS ±15%。docs/07 §八、docs/reports/index-calibration-*.md

    夜间 pvlib 给 NaN（airmass 无定义），这里归 0；输入本身缺测的时刻保持 NaN。
    """
    times = pd.DatetimeIndex(solar_zenith.index)
    valid = dni.notna() & ghi.notna() & dhi.notna()
    total = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=solar_zenith,
        solar_azimuth=solar_azimuth,
        dni=dni.fillna(0.0),
        ghi=ghi.fillna(0.0),
        dhi=dhi.fillna(0.0),
        dni_extra=pvlib.irradiance.get_extra_radiation(times),
        airmass=pvlib.atmosphere.get_relative_airmass(solar_zenith),
        model=settings.pv_sky_diffuse_model,
    )
    return total["poa_global"].fillna(0.0).where(valid, np.nan)


def bifacial_poa(
    *,
    tilt: float | pd.Series,
    azimuth: float | pd.Series,
    solar_zenith: pd.Series,
    solar_azimuth: pd.Series,
    dni: pd.Series,
    ghi: pd.Series,
    dhi: pd.Series,
) -> pd.Series:
    """双面组件的有效辐照：pvlib infinite_sheds，背面按双面率折算进 poa_global。

    几何按行间距比 gcr 归一化（宽度 1）：行距 1/gcr、离地高度 0.6。未用实测校准，
    增益量级 5–12%，与行业经验一致；docs/07 §2.1。
    """
    from pvlib.bifacial import infinite_sheds

    times = pd.DatetimeIndex(solar_zenith.index)
    valid = dni.notna() & ghi.notna() & dhi.notna()
    res = infinite_sheds.get_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=solar_zenith,
        solar_azimuth=solar_azimuth,
        gcr=settings.pv_tracking_gcr,
        height=0.6,
        pitch=1.0 / settings.pv_tracking_gcr,
        ghi=ghi.fillna(0.0),
        dhi=dhi.fillna(0.0),
        dni=dni.fillna(0.0),
        albedo=settings.pv_albedo,
        model="haydavies",
        dni_extra=pvlib.irradiance.get_extra_radiation(times),
        bifaciality=settings.pv_bifaciality,
    )
    return pd.Series(res["poa_global"], index=times).fillna(0.0).where(valid, np.nan)


def surface_orientation(
    inp: PvInputs, pos: pd.DataFrame
) -> tuple[float | pd.Series, float | pd.Series]:
    """固定支架用站点倾角 / 方位角；单轴跟踪按 pvlib singleaxis 逐时刻求支架姿态。

    南北向水平轴、最大转角与背轨参数取 settings；夜间 pvlib 给 NaN，按平放处理。
    """
    if inp.mounting != "single_axis":
        return inp.tilt, inp.azimuth
    tr = pvlib.tracking.singleaxis(
        pos["apparent_zenith"],
        pos["azimuth"],
        axis_tilt=0.0,
        axis_azimuth=180.0,
        max_angle=settings.pv_tracking_max_angle,
        backtrack=True,
        gcr=settings.pv_tracking_gcr,
    )
    return tr["surface_tilt"].fillna(0.0), tr["surface_azimuth"].fillna(180.0)


def hourly_power(
    inp: PvInputs,
    *,
    ghi: pd.Series | None = None,
    dni: pd.Series | None = None,
    dhi: pd.Series | None = None,
    temp_air: pd.Series | None = None,
    wind_speed: pd.Series | None = None,
) -> pd.Series:
    """逐区间交流出力（kW）。关键字参数用于替换某个输入（指数分母、归因）。

    太阳位置取区间中点：辐射是前一区间均值，标在区间末。见 solar 模块说明。
    """
    ghi = inp.ghi if ghi is None else ghi
    dni = inp.dni if dni is None else dni
    dhi = inp.dhi if dhi is None else dhi
    temp_air = inp.temp_air if temp_air is None else temp_air
    wind_speed = inp.wind_speed if wind_speed is None else wind_speed

    pos = solar.solar_position_interval(
        inp.latitude, inp.longitude, inp.tz, inp.times, inp.step_minutes
    )
    tilt, azimuth = surface_orientation(inp, pos)
    poa_fn = bifacial_poa if inp.bifacial else poa_from_components
    poa = poa_fn(
        tilt=tilt,
        azimuth=azimuth,
        solar_zenith=pos["apparent_zenith"],
        solar_azimuth=pos["azimuth"],
        dni=dni,
        ghi=ghi,
        dhi=dhi,
    )
    return ac_power(
        poa_global=poa,
        temp_air=temp_air,
        wind_speed=wind_speed,
        capacity_kw=inp.capacity_kw,
        dc_capacity_kw=inp.dc_capacity_kw,
    )


def daily_energy_kwh(power_kw: pd.Series, step_minutes: int = 60) -> float:
    """逐区间功率积分为日发电量（kW × 小时）；任一区间缺测则为 NaN。"""
    return float(power_kw.sum(skipna=False)) * step_minutes / 60.0
