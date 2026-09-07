"""太阳几何与晴空辐射。

实现一律用 pvlib，不手写公式 —— docs/07 附录 A 是口径定义不是实现。
"""

from datetime import date, datetime

import pandas as pd
import pvlib


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


def clearsky(latitude: float, longitude: float, tz: str, times: pd.DatetimeIndex) -> pd.DataFrame:
    """晴空辐射，含 ghi / dni / dhi。用作环境指数的理想基准。"""
    return location(latitude, longitude, tz).get_clearsky(times, model="ineichen")


def solar_position(
    latitude: float, longitude: float, tz: str, times: pd.DatetimeIndex
) -> pd.DataFrame:
    return location(latitude, longitude, tz).get_solarposition(times)
