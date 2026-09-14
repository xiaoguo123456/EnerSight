"""24 小时气象趋势按所选日期取数，与首页七天预测一一对应。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from httpx import AsyncClient

from app.schemas.home import TrendMetric, TrendRange
from app.services import home, weather
from tests.fixtures_forecast import TZ, make_forecast


def _forecast() -> weather.Forecast:
    now = datetime.now(ZoneInfo(TZ))
    yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return weather.parse_forecast(make_forecast(start_date=yesterday.replace(tzinfo=None)))


def test_所选日期的趋势从当天零点到次日零点():
    fc = _forecast()
    today = fc.current_hour().normalize()
    for offset in (0, 2, 6):
        series = home.build_trend(fc, TrendMetric.RADIATION, TrendRange.H24, offset)
        start = today + pd.Timedelta(days=offset)
        assert len(series.points) == 97
        assert pd.Timestamp(series.points[0].time) == start
        assert pd.Timestamp(series.points[-1].time) == start + pd.Timedelta(hours=24)


def test_默认仍是今日():
    fc = _forecast()
    default = home.build_trend(fc, TrendMetric.CLOUD_COVER)
    explicit = home.build_trend(fc, TrendMetric.CLOUD_COVER, TrendRange.H24, 0)
    assert default.points == explicit.points


def test_风电轮毂高度风速与发电预测同一换算():
    from app.metrics import wind

    fc = _forecast()
    series = home.build_trend(fc, TrendMetric.HUB_WIND_SPEED, TrendRange.H24, 1, 120.0)
    window = fc.day_with_midnight(1)
    levels = {h: window[c].astype(float) for h, c in wind.LEVEL_COLUMNS.items() if c in window}
    expected, _ = wind.hub_wind_speed(levels, 120.0)
    assert series.hub_height == 120.0 and series.unit == "m/s" and len(series.points) == 97
    assert [p.value for p in series.points] == [home._num(v) for v in expected]
    assert home.build_trend(fc, TrendMetric.WIND_SPEED).hub_height is None


def test_光伏站不提供轮毂高度风速():
    import pytest

    from app.errors import ApiError

    with pytest.raises(ApiError):
        home.build_trend(_forecast(), TrendMetric.HUB_WIND_SPEED)


async def test_超出七天预测范围的日期被拒绝(client: AsyncClient):
    r = await client.get("/v1/trends", params={"station_id": "x", "day_offset": 7})
    assert r.status_code in (400, 422)
