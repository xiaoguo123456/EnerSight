"""合成的 Open-Meteo 预报响应，形状与真实返回一致（192 点，昨日 00:00 起）。"""

import math
from datetime import datetime, timedelta

import pandas as pd

from app.metrics.solar import clearsky_hourly_mean

TZ = "Asia/Shanghai"


def make_forecast(
    *,
    start_date: datetime,
    peak_today: float = 800.0,
    peak_yesterday: float = 600.0,
    cloud_today: float = 30.0,
    cloud_yesterday: float = 50.0,
    wind_today: float = 4.5,
    wind_yesterday: float = 3.0,
    temp_today: float = 28.0,
    code_now: int = 0,
    code_later: int = 2,
    days: int = 8,
    elevation: float = 5.0,
    minutely: bool = True,
) -> dict:
    """start_date 是「昨日 00:00」（naive，当地时间）。默认 昨日 + 7 天 = 192 点，与线上一致。

    minutely=True 时另给 15 分钟序列（线上由上游插值，这里线性插值），elevation 决定气压。"""
    # 辐射按区间末标记的晴空小时均值合成，避免固定正弦整点曲线在早晚制造虚假 kt 下降。
    labels = pd.date_range(start_date, periods=days * 24, freq="h", tz=TZ)
    clear = clearsky_hourly_mean(31.3, 120.62, TZ, labels)["ghi"]
    normalized = clear / clear.groupby(labels.date).transform("max").clip(lower=1)
    times, t2m, app_t, rh, ws, wd, cc, code, swr, dr, dfr, dni, is_day = ([] for _ in range(13))
    for i in range(days * 24):
        ts = start_date + timedelta(hours=i)
        day = i // 24  # 0 昨日 1 今日 2 明日 ...
        h = ts.hour
        times.append(ts.strftime("%Y-%m-%dT%H:%M"))
        peak = peak_yesterday if day == 0 else peak_today
        cloud = cloud_yesterday if day == 0 else cloud_today
        wind = wind_yesterday if day == 0 else wind_today
        r = round(float(normalized.iloc[i]) * peak, 1)
        t2m.append(
            temp_today - 4 + 8 * math.sin(math.pi * max(0, h - 4) / 16) if day else temp_today - 2
        )
        app_t.append(t2m[-1] + 2)
        rh.append(60.0)
        ws.append(wind)
        wd.append(180.0)
        cc.append(cloud)
        code.append(code_now if h < 14 else code_later)
        swr.append(r)
        dr.append(round(r * 0.7, 1))
        dfr.append(round(r * 0.3, 1))
        dni.append(round(r * 0.9, 1))
        is_day.append(1 if 6 <= h <= 18 else 0)
    pressure = [round(1013.25 * (1 - 2.25577e-5 * elevation) ** 5.25588, 1)] * (days * 24)
    hourly = {
        "time": times,
        "temperature_2m": t2m,
        "apparent_temperature": app_t,
        "relative_humidity_2m": rh,
        "wind_speed_10m": ws,
        "wind_speed_80m": [round(v * 8**0.18, 2) for v in ws],
        "wind_speed_100m": [round(v * 10**0.18, 2) for v in ws],
        "wind_speed_120m": [round(v * 12**0.18, 2) for v in ws],
        "wind_direction_10m": wd,
        "cloud_cover": cc,
        "cloud_cover_low": cc,
        "cloud_cover_mid": [0.0] * (days * 24),
        "cloud_cover_high": [0.0] * (days * 24),
        "weather_code": code,
        "shortwave_radiation": swr,
        "direct_radiation": dr,
        "diffuse_radiation": dfr,
        "direct_normal_irradiance": dni,
        "is_day": is_day,
        "surface_pressure": pressure,
    }
    out = {
        "latitude": 31.3,
        "longitude": 120.62,
        "generationtime_ms": 0.1,
        "utc_offset_seconds": 28800,
        "timezone": TZ,
        "timezone_abbreviation": "CST",
        "elevation": elevation,
        "hourly_units": {"time": "iso8601", "wind_speed_10m": "m/s"},
        "hourly": hourly,
    }
    if minutely:
        n = days * 24
        pos = [i / 4 for i in range(n * 4)]
        base = list(range(n))

        def interp(values):
            arr = [float(v) for v in values]
            return [round(float(v), 3) for v in __import__("numpy").interp(pos, base, arr)]

        out["minutely_15"] = {
            "time": [
                (start_date + timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M")
                for i in range(n * 4)
            ],
            **{
                k: interp(hourly[k])
                for k in (
                    "temperature_2m",
                    "wind_speed_10m",
                    "wind_speed_80m",
                    "wind_speed_100m",
                    "wind_speed_120m",
                    "shortwave_radiation",
                    "diffuse_radiation",
                    "direct_normal_irradiance",
                    "surface_pressure",
                )
            },
            "weather_code": [code[min(n - 1, i // 4)] for i in range(n * 4)],
        }
    return out


def _unused() -> None:
    return None
