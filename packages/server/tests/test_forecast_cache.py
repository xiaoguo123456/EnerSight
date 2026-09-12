"""按模型批次缓存预报、15 分钟序列按需拉取、上游 429 降级。

上游每 6 小时才出一批，按时间片缓存会把同一份数据反复拉回来（每网格每天 96 次，
92 次是重复的）。这里锁住「同批次只回源一次、新批次立刻刷新」这条线。
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from httpx import Response

from app.errors import UpstreamUnavailable
from app.services import weather
from tests.fixtures_forecast import TZ, make_forecast

LAT, LON = 31.30, 120.62
BATCH_A = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
BATCH_B = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _meta(issued: datetime) -> dict:
    return {
        "last_run_initialisation_time": issued.timestamp(),
        "last_run_availability_time": (issued + timedelta(hours=6)).timestamp(),
        "update_interval_seconds": 21600,
    }


def _yesterday_midnight() -> datetime:
    now = datetime.now(ZoneInfo(TZ))
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


@pytest.fixture(autouse=True)
def _fresh():
    weather.clear_cache()
    yield
    weather.clear_cache()


class _Upstream:
    """按请求参数区分逐小时与 15 分钟两种请求，分别计数。"""

    def __init__(self, issued: datetime | None = BATCH_A, quarter_status: int = 200) -> None:
        self.issued = issued
        self.quarter_status = quarter_status
        self.hourly_calls = 0
        self.quarter_calls = 0
        self.quarter_days: int | None = None
        self.meta_calls = 0
        full = make_forecast(start_date=_yesterday_midnight())
        self._hourly = {k: v for k, v in full.items() if k != "minutely_15"}
        self._quarter = {"timezone": TZ, "minutely_15": full["minutely_15"]}

    def forecast(self, request: httpx.Request) -> Response:
        if "minutely_15" in request.url.params:
            self.quarter_calls += 1
            self.quarter_days = int(request.url.params["forecast_days"])
            if self.quarter_status != 200:
                return Response(self.quarter_status)
            return Response(200, json=self._quarter)
        self.hourly_calls += 1
        return Response(200, json=self._hourly)

    def meta(self, _request: httpx.Request) -> Response:
        self.meta_calls += 1
        if self.issued is None:
            return Response(404)
        return Response(200, json=_meta(self.issued))


@pytest.fixture
def upstream():
    up = _Upstream()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta\.json").mock(side_effect=up.meta)
        mock.get(url__regex=r".*/v1/forecast.*").mock(side_effect=up.forecast)
        yield up


async def _get(up_client: httpx.AsyncClient, **kw) -> weather.Forecast:
    return await weather.get_forecast(up_client, LAT, LON, **kw)


class TestBatchCache:
    async def test_同一批次内只回源一次(self, upstream: _Upstream):
        async with httpx.AsyncClient() as http:
            for _ in range(5):
                # 元数据缓存到期也不该触发重拉：起报时刻没变，数据就没变
                weather._meta_cache.clear()
                await _get(http)
        assert upstream.hourly_calls == 1
        assert upstream.meta_calls == 5

    async def test_新批次落地立刻刷新(self, upstream: _Upstream):
        async with httpx.AsyncClient() as http:
            await _get(http)
            upstream.issued = BATCH_B
            weather._meta_cache.clear()  # 模拟元数据 TTL 到期，看到新批次
            fc = await _get(http)
        assert upstream.hourly_calls == 2
        assert fc.meta is not None
        assert fc.meta.issued_at == BATCH_B

    async def test_起报时刻进缓存键(self, upstream: _Upstream):
        async with httpx.AsyncClient() as http:
            fc = await _get(http)
        assert weather.batch_stamp(fc.meta) == BATCH_A.isoformat()

    async def test_元数据拿不到时退回时间片而不是固定键(self):
        """固定键会让这个网格的数据再也不刷新 —— 比多拉几次严重得多。"""
        a = weather.batch_stamp(None)
        assert a.startswith("unknown-")
        # 时间片随时钟推进，不是常量
        slice_now = int(datetime.now(UTC).timestamp()) // 600
        assert a == f"unknown-{slice_now}"

    async def test_无元数据时仍按时间片回源(self, upstream: _Upstream):
        upstream.issued = None
        async with httpx.AsyncClient() as http:
            fc = await _get(http)
            await _get(http)
        assert upstream.hourly_calls == 1  # 同一时间片内仍然命中
        assert fc.basis().issued_at is None  # 不拿拉取时间冒充起报


class TestLazyQuarter:
    async def test_默认不拉15分钟序列(self, upstream: _Upstream):
        async with httpx.AsyncClient() as http:
            fc = await _get(http)
        assert upstream.quarter_calls == 0
        assert fc.quarter is None

    async def test_fine才拉且与逐小时分开计费(self, upstream: _Upstream):
        async with httpx.AsyncClient() as http:
            fc = await _get(http, fine=True)
        assert upstream.hourly_calls == 1
        assert upstream.quarter_calls == 1
        assert fc.quarter is not None

    async def test_fine的结果不污染普通缓存(self, upstream: _Upstream):
        async with httpx.AsyncClient() as http:
            await _get(http, fine=True)
            plain = await _get(http)
        assert plain.quarter is None  # 后台任务不该因为别人拉过而多带一份
        assert upstream.hourly_calls == 1

    async def test_15分钟序列同批次内只拉一次(self, upstream: _Upstream):
        async with httpx.AsyncClient() as http:
            await _get(http, fine=True)
            await _get(http, fine=True)
        assert upstream.quarter_calls == 1

    async def test_15分钟序列天数覆盖到最后一个细粒度日的末标签(self, upstream: _Upstream):
        """细粒度最后一天的区间末标签落在次日 00:00，天数少一天那一天会整天变 null。"""
        from app.config import settings

        async with httpx.AsyncClient() as http:
            await _get(http, fine=True)
        assert upstream.quarter_days is not None
        assert upstream.quarter_days > settings.outlook_fine_days

    async def test_15分钟序列失败时退回逐小时(self, upstream: _Upstream):
        upstream.quarter_status = 500
        async with httpx.AsyncClient() as http:
            fc = await _get(http, fine=True)
        assert fc.quarter is None  # 降级，不是抛错
        assert not fc.hourly.empty  # 主链路不受影响


class TestUpstreamErrors:
    async def test_429映射为可重试的上游不可用(self):
        from app.providers.open_meteo import OpenMeteoProvider

        with respx.mock:
            respx.get(url__regex=r".*/v1/forecast.*").mock(return_value=Response(429))
            async with httpx.AsyncClient() as http:
                with pytest.raises(UpstreamUnavailable) as exc:
                    await OpenMeteoProvider(http).forecast(LAT, LON)
        assert exc.value.code == "UPSTREAM_UNAVAILABLE"
        assert exc.value.status == 502
        assert "配额" in exc.value.message


class TestFieldBudget:
    async def test_字段数控制在计费基准内(self):
        """Open-Meteo 超过 10 个变量按比例计为多次调用。加字段前先想清楚谁在读。"""
        from app.providers.open_meteo import HOURLY_FIELDS, MINUTELY_FIELDS

        assert len(HOURLY_FIELDS) <= 15
        assert len(MINUTELY_FIELDS) <= 10
        assert len(set(HOURLY_FIELDS)) == len(HOURLY_FIELDS)

    async def test_出力模型必需字段一个都不少(self):
        """删字段省钱，但删到模型输入就会变成「数据获取中」。"""
        from app.providers.open_meteo import HOURLY_FIELDS
        from app.services.fleet_prediction import FIELDS as MODEL_FIELDS

        assert set(MODEL_FIELDS) <= set(HOURLY_FIELDS)
