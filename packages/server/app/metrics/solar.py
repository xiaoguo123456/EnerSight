"""太阳几何与晴空辐射。

实现一律用 pvlib，不手写公式 —— docs/07 附录 A 是口径定义不是实现。

时间约定（docs/04 §二）：Open-Meteo 的小时辐射是「前一小时平均值」，标在区间末。
标注 13:00 的值对应 12:00–13:00。因此与之配合的太阳位置要取区间中点 12:30，
晴空基准也要取同一区间的均值，否则日出日落两端的逐时功率会系统性偏差。
"""

from datetime import date, datetime

import numpy as np
import pandas as pd
import pvlib

# 小时均值用 6 个子步（10 分钟）做中点求积，够用且便宜
_MEAN_STEPS = 6


def location(latitude: float, longitude: float, tz: str) -> pvlib.location.Location:
    return pvlib.location.Location(latitude, longitude, tz=tz)


def daylight_window(
    latitude: float, longitude: float, tz: str, day: date
) -> tuple[datetime, datetime]:
    """当日日出日落。

    日间时段不是固定的 06:00–18:00 —— 固定窗口在高纬度和冬季会把夜间
    数据算进日间平均。见 docs/07 §1.7
    """
    times = pd.DatetimeIndex([pd.Timestamp(day, tz=tz)])
    res = pvlib.solarposition.sun_rise_set_transit_spa(times, latitude, longitude)
    # 秒级精度足够 —— 用于确定聚合窗口，不是天文计算
    sunrise = res["sunrise"].iloc[0].round("s").to_pydatetime()
    sunset = res["sunset"].iloc[0].round("s").to_pydatetime()
    return sunrise, sunset


def interval_centers(times: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """区间末标注的小时时刻 → 区间中点（提前 30 分钟）。"""
    return pd.DatetimeIndex(times) - pd.Timedelta(minutes=30)


def clearsky(latitude: float, longitude: float, tz: str, times: pd.DatetimeIndex) -> pd.DataFrame:
    """瞬时晴空辐射，含 ghi / dni / dhi。"""
    return location(latitude, longitude, tz).get_clearsky(times, model="ineichen")


def clearsky_hourly_mean(
    latitude: float, longitude: float, tz: str, times: pd.DatetimeIndex
) -> pd.DataFrame:
    """与 Open-Meteo 同口径的晴空辐射：每个时刻取其前一小时的均值。

    用作环境指数的理想基准与预警的晴空指数分母。
    """
    times = pd.DatetimeIndex(times)
    offsets = [pd.Timedelta(minutes=-(i + 0.5) * 60 / _MEAN_STEPS) for i in range(_MEAN_STEPS)]
    fine = pd.DatetimeIndex([t + o for t in times for o in offsets])
    cs = location(latitude, longitude, tz).get_clearsky(fine, model="ineichen")
    out = cs.groupby(np.repeat(np.arange(len(times)), _MEAN_STEPS)).mean()
    out.index = times
    return out


def solar_position(
    latitude: float, longitude: float, tz: str, times: pd.DatetimeIndex
) -> pd.DataFrame:
    """瞬时太阳位置。"""
    return location(latitude, longitude, tz).get_solarposition(times)


def solar_position_interval(
    latitude: float, longitude: float, tz: str, times: pd.DatetimeIndex
) -> pd.DataFrame:
    """小时均值辐射对应的太阳位置：取区间中点计算，索引仍按原时刻标注。"""
    times = pd.DatetimeIndex(times)
    pos = solar_position(latitude, longitude, tz, interval_centers(times))
    pos.index = times
    return pos
