"""单点预报按回源时段与模型批次缓存、15 分钟序列统一请求、上游 429 降级。

上游每 6 小时才出一批，短时间片缓存会把同一份数据反复拉回来（每网格每天 96 次，
92 次是重复的）。这里锁住「每个坐标每天最多回源 4 次、跨时段批次没变不重拉」这条线。
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from httpx import Response

from app.errors import UpstreamRateLimited, UpstreamUnavailable
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
        assert upstream.quarter_calls == 1
        assert upstream.hourly_calls == 0
        assert upstream.meta_calls == 5

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
        assert upstream.quarter_calls == 1
        assert upstream.hourly_calls == 0  # 同一时间片内仍然命中
        assert fc.basis().issued_at is None  # 不拿拉取时间冒充起报


class _Clock(datetime):
    """weather 模块里的 datetime：拉取时刻与 Forecast.now 共用同一个可拨动的时钟。"""

    current = datetime(2026, 9, 14, 0, 30, tzinfo=ZoneInfo(TZ))

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz is not None else cls.current.replace(tzinfo=None)


@pytest.fixture
def clock(monkeypatch):
    _Clock.current = datetime(2026, 9, 14, 0, 30, tzinfo=ZoneInfo(TZ))
    monkeypatch.setattr(weather, "datetime", _Clock)
    return _Clock


class TestDailyRefreshSlots:
    """每个坐标每天最多回源 4 次：时段内不追新批次，进入新时段批次没变也不重拉。"""

    async def test_同一时段内新批次不回源(self, upstream: _Upstream, clock):
        async with httpx.AsyncClient() as http:
            await _get(http)
            upstream.issued = BATCH_B
            weather._meta_cache.clear()
            clock.current += timedelta(hours=5)  # 05:30，仍在 00–06 时段
            fc = await _get(http)
        assert upstream.quarter_calls == 1
        assert fc.meta is not None and fc.meta.issued_at == BATCH_A

    async def test_进入新时段批次没变不回源_变了才刷新(self, upstream: _Upstream, clock):
        async with httpx.AsyncClient() as http:
            await _get(http)
            clock.current += timedelta(hours=6)
            weather._meta_cache.clear()
            await _get(http)
            assert upstream.quarter_calls == 1
            upstream.issued = BATCH_B
            weather._meta_cache.clear()
            fc = await _get(http)
        assert upstream.quarter_calls == 2
        assert fc.meta is not None and fc.meta.issued_at == BATCH_B

    @pytest.mark.parametrize("meta_known", [True, False])
    async def test_批次频繁变化或元数据缺失一天也最多回源四次(
        self, upstream: _Upstream, clock, meta_known
    ):
        async with httpx.AsyncClient() as http:
            for hour in range(24):
                upstream.issued = BATCH_A + timedelta(hours=hour) if meta_known else None
                weather._meta_cache.clear()
                await _get(http)
                clock.current += timedelta(hours=1)
        assert upstream.quarter_calls == 4


class TestUnifiedQuarter:
    async def test_所有消费者共用一次15分钟请求(self, upstream):
        async with httpx.AsyncClient() as http:
            first = await _get(http)
            second = await _get(http)
        assert first is second
        assert first.step_minutes == 15 and not first.data.empty
        assert upstream.hourly_calls == 0 and upstream.quarter_calls == 1
        assert upstream.quarter_days == 8

    async def test_15分钟失败不偷偷请求小时数据且下次可重试(self, upstream, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "upstream_backoff_seconds", 0)
        upstream.quarter_status = 500
        async with httpx.AsyncClient() as http:
            with pytest.raises(UpstreamUnavailable):
                await _get(http)
            upstream.quarter_status = 200
            fc = await _get(http)
        assert fc.step_minutes == 15
        assert upstream.hourly_calls == 0
        assert upstream.quarter_calls == settings.upstream_retries + 1


class TestUpstreamErrors:
    async def test_429映射为不自动重试的配额错误(self):
        from app.providers.open_meteo import OpenMeteoProvider

        with respx.mock:
            respx.get(url__regex=r".*/v1/forecast.*").mock(return_value=Response(429))
            async with httpx.AsyncClient() as http:
                with pytest.raises(UpstreamRateLimited) as exc:
                    await OpenMeteoProvider(http).forecast(LAT, LON)
        assert exc.value.code == "UPSTREAM_RATE_LIMITED"
        assert exc.value.status == 503
        assert "配额" in exc.value.message


class TestRetry:
    """上游拉取一律带退避重试、限并发（CLAUDE.md）。但只对重试有意义的错误重试。"""

    @pytest.fixture(autouse=True)
    def _no_backoff(self, monkeypatch: pytest.MonkeyPatch):
        from app.config import settings

        monkeypatch.setattr(settings, "upstream_backoff_seconds", 0.0)

    async def _fetch(self, responses: list) -> tuple[int, object]:
        """responses 逐次返回；返回 (实际请求次数, 结果或异常)。"""
        from app.providers.open_meteo import OpenMeteoProvider

        calls = {"n": 0}

        def side_effect(_request):
            calls["n"] += 1
            item = responses[min(calls["n"] - 1, len(responses) - 1)]
            if isinstance(item, Exception):
                raise item
            return item

        with respx.mock:
            respx.get(url__regex=r".*/v1/forecast.*").mock(side_effect=side_effect)
            async with httpx.AsyncClient() as http:
                try:
                    # 先 await 再读计数：元组是从左到右求值的
                    out: object = await OpenMeteoProvider(http).forecast(LAT, LON)
                except Exception as exc:  # noqa: BLE001
                    out = exc
        return calls["n"], out

    async def test_传输错误重试后成功(self):
        ok = Response(200, json=make_forecast(start_date=_yesterday_midnight()))
        n, out = await self._fetch([httpx.ConnectError("TLS 拒连"), ok])
        assert n == 2
        assert isinstance(out, dict)

    async def test_5xx重试后成功(self):
        ok = Response(200, json=make_forecast(start_date=_yesterday_midnight()))
        n, out = await self._fetch([Response(502), ok])
        assert n == 2
        assert isinstance(out, dict)

    async def test_一直失败则报上游不可用(self):
        n, out = await self._fetch([httpx.ConnectError("boom")])
        assert n == 3  # upstream_retries
        assert isinstance(out, UpstreamUnavailable)

    async def test_429不重试(self):
        """额度按天算，立刻重试只会烧得更快。"""
        n, out = await self._fetch([Response(429)])
        assert n == 1
        assert isinstance(out, UpstreamRateLimited)
        assert "配额" in out.message

    async def test_400不重试(self):
        """坐标超出覆盖范围，重试多少次都一样。"""
        from app.errors import DataUnavailable

        n, out = await self._fetch([Response(400)])
        assert n == 1
        assert isinstance(out, DataUnavailable)


class TestFieldBudget:
    async def test_字段数控制在计费基准内(self):
        """Open-Meteo 超过 10 个变量按比例计为多次调用。加字段前先想清楚谁在读。"""
        from app.providers.open_meteo import HOURLY_FIELDS, MINUTELY_FIELDS

        assert len(HOURLY_FIELDS) <= 15
        assert len(MINUTELY_FIELDS) == 15
        assert set(MINUTELY_FIELDS) == set(HOURLY_FIELDS)
        assert len(set(HOURLY_FIELDS)) == len(HOURLY_FIELDS)

    async def test_出力模型必需字段一个都不少(self):
        """删字段省钱，但删到模型输入就会变成「数据获取中」。"""
        from app.providers.open_meteo import HOURLY_FIELDS
        from app.services.fleet_prediction import FIELDS as MODEL_FIELDS

        assert set(MODEL_FIELDS) <= set(HOURLY_FIELDS)
