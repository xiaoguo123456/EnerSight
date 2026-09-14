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


async def test_超出七天预测范围的日期被拒绝(client: AsyncClient):
    r = await client.get("/v1/trends", params={"station_id": "x", "day_offset": 7})
    assert r.status_code in (400, 422)
