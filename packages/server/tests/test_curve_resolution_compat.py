"""线上旧版小程序兼容：不带 X-Resolution-Minutes: 15 的请求拿逐小时曲线。docs/06 §2.8"""

import pytest
from httpx import AsyncClient

from app import curve_resolution as cr
from app.schemas.home import TrendMetric, TrendPoint, TrendRange, TrendSeries
from app.schemas.prediction import PowerPoint
from tests.test_home import WIND, _create, _fresh_cache, open_meteo  # noqa: F401

OLD_CLIENT = {cr.HEADER: ""}


def _quarters(day: str = "2026-09-14") -> list[str]:
    return [f"{day}T{h:02d}:{m:02d}:00+08:00" for h in range(24) for m in (0, 15, 30, 45)]


def test_功率按小时四格平均_缺一格为null_电量不变():
    times = _quarters()
    pts = [PowerPoint(time=t, value=float(i)) for i, t in enumerate(times)]
    out = cr.hourly_power(pts)
    assert len(out) == 24
    assert out[0].time == "2026-09-14T00:00:00+08:00" and out[0].value == 1.5
    assert sum(p.value or 0 for p in out) == pytest.approx(sum(p.value or 0 for p in pts) / 4)

    pts[5] = PowerPoint(time=times[5], value=None)
    assert cr.hourly_power(pts)[1].value is None


def _series(metric: TrendMetric) -> TrendSeries:
    times = [*_quarters(), "2026-09-15T00:00:00+08:00"]
    return TrendSeries(
        metric=metric,
        unit="",
        range=TrendRange.H24,
        y_max=None,
        points=[TrendPoint(time=t, value=float(i)) for i, t in enumerate(times)],
        resolution_minutes=15,
        hub_height=None,
    )


def test_趋势瞬时量取整点_辐射取区间末前四格平均():
    wind = cr.hourly_trend(_series(TrendMetric.WIND_SPEED))
    assert wind.resolution_minutes == 60 and len(wind.points) == 25
    assert [p.value for p in wind.points[:3]] == [0.0, 4.0, 8.0]
    assert wind.points[-1].time == "2026-09-15T00:00:00+08:00"

    rad = cr.hourly_trend(_series(TrendMetric.RADIATION))
    # 首点只有自身；01:00 覆盖 00:15–01:00 四格（索引 1–4）
    assert [p.value for p in rad.points[:3]] == [0.0, 2.5, 6.5]


def test_请求上下文外不换算():
    s = _series(TrendMetric.CLOUD_COVER)
    assert cr.adapt(s) is s


async def test_旧版客户端拿逐小时_新版不变_不额外请求上游(
    client: AsyncClient,
    open_meteo,  # noqa: F811  夹具复用 test_home
):
    sid = await _create(client, WIND)
    params = {"station_id": sid}
    old = (await client.get("/v1/home", params=params, headers=OLD_CLIENT)).json()["data"]
    new = (await client.get("/v1/home", params=params)).json()["data"]

    assert old["trends"]["resolution_minutes"] == 60 and len(old["trends"]["points"]) == 25
    assert new["trends"]["resolution_minutes"] == 15 and len(new["trends"]["points"]) == 97
    if new["prediction"] is not None:
        assert len(old["prediction"]["power_kw"]) == 24 and len(new["prediction"]["power_kw"]) == 96
        assert old["prediction"]["energy_kwh"] == new["prediction"]["energy_kwh"]

    trend = await client.get(
        "/v1/trends", params={**params, "metric": "wind_speed"}, headers=OLD_CLIENT
    )
    assert len(trend.json()["data"]["points"]) == 25
    detail = await client.get(f"/v1/stations/{sid}/detail", headers=OLD_CLIENT)
    assert len(detail.json()["data"]["trends"]["points"]) == 25
    assert open_meteo.call_count == 1
