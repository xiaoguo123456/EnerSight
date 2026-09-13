from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import respx
from httpx import AsyncClient, Response

from app.ai import generate as ai
from app.ai.input import ReportInput, extract_numbers
from app.ai.schema import AIReport, ReportPeriod
from app.config import settings
from app.main import app
from app.services import weather
from tests.fixtures_forecast import TZ, make_forecast

SUZHOU = {
    "name": "苏州光伏站",
    "type": "solar",
    "latitude": 31.30,
    "longitude": 120.62,
    "capacity": 500,
}


def _yesterday_midnight() -> datetime:
    now = datetime.now(ZoneInfo(TZ))
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


@pytest.fixture(autouse=True)
def _fresh():
    weather.clear_cache()
    ai.reset_provider()
    yield
    weather.clear_cache()
    ai.reset_provider()


@pytest.fixture
def open_meteo():
    with respx.mock(assert_all_called=False) as mock:
        yield mock.get(url__regex=r".*open-meteo.*").mock(
            return_value=Response(200, json=make_forecast(start_date=_yesterday_midnight()))
        )


def _report(**kw) -> AIReport:
    base = dict(
        verdict_title="今日适宜发电",
        verdict_detail="环境指数 82 分。",
        periods=[
            ReportPeriod(
                period="morning", weather_summary="晴", generation_impact="良好", level="good"
            ),
            ReportPeriod(
                period="afternoon",
                weather_summary="多云",
                generation_impact="下降",
                level="warning",
            ),
            ReportPeriod(
                period="evening", weather_summary="晴", generation_impact="无影响", level="good"
            ),
        ],
        risk_title=None,
        risk_detail=None,
        suggestions=["按计划运行。", "常规巡检。"],
    )
    base.update(kw)
    return AIReport(**base)


class TestNumericConsistency:
    """数值一致性校验是最重要的一条：输出里的数字必须都来自输入。docs/08 §八"""

    def test_数字都在输入里通过(self):
        allowed = extract_numbers("今日环境指数：82 分 平均辐射 520 W/m² 14:30")
        assert ai.numbers_consistent(
            _report(verdict_detail="环境指数 82 分，14:30 后下降"), allowed
        )

    def test_编造数字判定为幻觉(self):
        allowed = extract_numbers("今日环境指数：82 分")
        assert not ai.numbers_consistent(_report(verdict_detail="预计发电 6320 kWh"), allowed)

    def test_千分位与小数不影响匹配(self):
        allowed = extract_numbers("日发电 2,400 kWh，等效 4.8 小时")
        assert ai.numbers_consistent(_report(verdict_detail="发电 2400 kWh，4.8 小时"), allowed)
        assert ai.numbers_consistent(
            _report(verdict_detail="等效 4.80 小时，12.0 点"), {"4.8", "12"}
        )

    def test_整数末尾的零不能被吞掉(self):
        """输入有「2 分」不代表输出能写「20%」「100 kWh」"""
        allowed = extract_numbers("温度损失 -2 分，06:00–12:00")
        assert not ai.numbers_consistent(_report(verdict_detail="预计下降 20%"), allowed)
        assert not ai.numbers_consistent(_report(verdict_detail="发电 100 kWh"), allowed)
        assert ai.numbers_consistent(_report(verdict_detail="下降 2 分，12 点前"), allowed)


class TestFallback:
    async def test_provider异常走规则模板(self, monkeypatch):
        class Broken:
            name = "claude"

            async def generate_report(self, inp):
                raise RuntimeError("boom")

        monkeypatch.setattr(ai, "_provider", Broken())
        inp = _dummy_input()
        g = await ai.generate(inp)
        assert g.is_fallback is True and g.provider == "rule"
        assert g.report.verdict_title

    async def test_幻觉走规则模板(self, monkeypatch):
        class Hallucinating:
            name = "claude"

            async def generate_report(self, inp):
                return _report(verdict_detail="明日预计发电 99999 kWh")

        monkeypatch.setattr(ai, "_provider", Hallucinating())
        g = await ai.generate(_dummy_input())
        assert g.is_fallback is True

    async def test_规则provider不算降级(self):
        g = await ai.generate(_dummy_input())
        assert g.is_fallback is False and g.provider == "rule"


def _dummy_input() -> ReportInput:
    inp = ReportInput(
        station_name="苏州光伏站",
        station_type="solar",
        capacity_kw=500,
        address=None,
        score=82.0,
        level="good",
        attribution=[("radiation", -12.0, "云层使辐照降至晴空的 85%")],
        periods=[
            {
                "key": "morning",
                "label": "上午",
                "range": "06:00–12:00",
                "weather": "晴",
                "avg_radiation": 500.0,
                "avg_cloud": 20.0,
            },
            {
                "key": "afternoon",
                "label": "下午",
                "range": "12:00–18:00",
                "weather": "多云",
                "avg_radiation": 380.0,
                "avg_cloud": 55.0,
            },
            {
                "key": "evening",
                "label": "晚间",
                "range": "18:00–24:00",
                "weather": "晴",
                "avg_radiation": 0.0,
                "avg_cloud": 30.0,
            },
        ],
        alert=None,
        daily_kwh=2400.0,
        equivalent_hours=4.8,
    )
    inp.numbers = extract_numbers(inp.render())
    return inp


class TestApi:
    async def test_首访生成并落库再访读缓存(self, client: AsyncClient, open_meteo, monkeypatch):
        calls = {"n": 0}
        real = ai.generate

        async def counting(inp):
            calls["n"] += 1
            return await real(inp)

        monkeypatch.setattr(ai, "generate", counting)
        from app.services import reports as svc

        monkeypatch.setattr(svc.ai, "generate", counting)

        sid = (await client.post("/v1/stations", json=SUZHOU)).json()["data"]["id"]
        r1 = await client.get(f"/v1/reports/{sid}")
        assert r1.status_code == 200, r1.text
        r2 = await client.get(f"/v1/reports/{sid}")
        assert calls["n"] == 1  # 第二次读缓存
        d = r2.json()["data"]
        assert d["station"]["id"] == sid
        assert len(d["periods"]) == 3 and d["periods"][0]["time_range"] == "06:00 – 12:00"
        assert d["is_fallback"] is False
        assert d["summary"]["generation"]["value"] > 0
        assert "年" in d["report_date"] and ":" in d["generated_at"]

    async def test_摘要四项来自计算(self, client: AsyncClient, open_meteo):
        sid = (await client.post("/v1/stations", json=SUZHOU)).json()["data"]["id"]
        s = (await client.get(f"/v1/reports/{sid}")).json()["data"]["summary"]
        g = s["generation"]["value"]
        assert s["equivalent_hours"]["value"] == pytest.approx(g / 500, rel=1e-2)
        assert s["co2_reduction"]["value"] == pytest.approx(
            g * settings.co2_factor_kg_per_kwh, rel=1e-2
        )
        assert s["estimated_revenue"]["value"] == pytest.approx(
            g * settings.tariff_yuan_per_kwh, abs=1
        )
        # 没有昨日记录，环比为 null
        assert s["generation"]["delta_percent"] is None

    async def test_别人的站点403(self, client: AsyncClient, open_meteo):
        from app import auth

        sid = (await client.post("/v1/stations", json=SUZHOU)).json()["data"]["id"]
        other, _ = auth.issue_token("someone-else")
        r = await client.get(f"/v1/reports/{sid}", headers={"Authorization": f"Bearer {other}"})
        assert r.status_code == 403


class TestReferenceWording:
    def test_规则不把资源与设备效率混为一谈(self):
        from app.ai.rule_provider import render

        report = render(_dummy_input())
        assert "效率可能下降" not in report.periods[1].generation_impact
        assert "次日请查看新预报" in report.periods[2].generation_impact
        assert "满负荷" not in " ".join(report.suggestions)
        assert "不代表设备健康" in report.verdict_detail


def _fc(**kw):
    return weather.parse_forecast(make_forecast(start_date=_yesterday_midnight(), **kw))


def _station_model():
    from app.models import Station

    return Station(
        id="s1",
        owner_id="u",
        name="苏州光伏站",
        type="solar",
        latitude=31.3,
        longitude=120.62,
        capacity_kw=500,
    )


def _index():
    from app.metrics.index import IndexResult
    from app.schemas.common import IndexLevel

    return IndexResult(
        score=80.0, level=IndexLevel.GOOD, actual_kwh=1000.0, ideal_kwh=1250.0, attribution=[]
    )


class TestReportInput:
    def test_天气码全缺测不崩也不写出nan(self):
        """mode() 在全缺测时是空 Series，直接取 iloc[0] 会抛 IndexError，整份报告生不出来"""
        from app.ai.input import build_input

        fc = _fc()
        for col in ("weather_code", "shortwave_radiation", "cloud_cover"):
            fc.data[col] = float("nan")
        inp = build_input(_station_model(), fc, _index(), 1000.0, None)
        assert [p["weather"] for p in inp.periods] == ["—"] * 3
        assert all(p["avg_radiation"] is None and p["avg_cloud"] is None for p in inp.periods)
        assert "nan" not in inp.render().lower()

    def test_缺测时规则模板照样出报告(self):
        from app.ai.input import build_input
        from app.ai.rule_provider import render

        fc = _fc()
        for col in ("weather_code", "shortwave_radiation", "cloud_cover"):
            fc.data[col] = float("nan")
        report = render(build_input(_station_model(), fc, _index(), 1000.0, None))
        assert len(report.periods) == 3

    def test_分时段辐射按区间末标签(self):
        """上午 06:00–12:00 的辐射是区间均值，对应标签 07:00 … 12:00；
        按整点 06 … 11 取会把最亮的一小时漏掉、把天亮前那格算进来。"""
        import pandas as pd

        from app.ai.input import build_input

        fc = _fc()
        inp = build_input(_station_model(), fc, _index(), 1000.0, None)
        day = fc.current_hour().normalize()
        rad = fc.data["shortwave_radiation"]

        def mean(h0, h1):
            return float(
                rad.loc[day + pd.Timedelta(hours=h0) : day + pd.Timedelta(hours=h1)].mean()
            )

        assert inp.periods[0]["avg_radiation"] == pytest.approx(mean(6.25, 12))
        assert inp.periods[0]["avg_radiation"] != pytest.approx(mean(6, 11))

    def test_云量仍按瞬时整点(self):
        import pandas as pd

        from app.ai.input import build_input

        fc = _fc()
        day = (
            weather.parse_forecast(make_forecast(start_date=_yesterday_midnight()))
            .current_hour()
            .normalize()
        )
        fc.data["cloud_cover"] = range(len(fc.data))
        inp = build_input(_station_model(), fc, _index(), 1000.0, None)
        expect = float(
            fc.data["cloud_cover"]
            .loc[day + pd.Timedelta(hours=6) : day + pd.Timedelta(hours=11, minutes=45)]
            .mean()
        )
        assert inp.periods[0]["avg_cloud"] == pytest.approx(expect)


class TestSummaryDelta:
    async def _insert(self, client, sid, days_ago: int, kwh: float):
        from app.db import get_session
        from app.models import DailyGeneration

        gen = app.dependency_overrides[get_session]()
        db = await gen.__anext__()
        try:
            db.add(
                DailyGeneration(
                    station_id=sid,
                    # 报告按站点时区取日期，不能依赖 CI 主机的 UTC 日期。
                    day=datetime.now(ZoneInfo(TZ)).date() - timedelta(days=days_ago),
                    kwh=kwh,
                )
            )
            await db.commit()
        finally:
            await gen.aclose()

    async def test_昨日无记录时不拿更早的一天冒充(self, client: AsyncClient, open_meteo):
        sid = (await client.post("/v1/stations", json=SUZHOU)).json()["data"]["id"]
        await self._insert(client, sid, 3, 100.0)
        s = (await client.get(f"/v1/reports/{sid}")).json()["data"]["summary"]
        assert s["generation"]["delta_percent"] is None

    async def test_有昨日记录才给环比(self, client: AsyncClient, open_meteo):
        sid = (await client.post("/v1/stations", json=SUZHOU)).json()["data"]["id"]
        await self._insert(client, sid, 3, 100.0)
        await self._insert(client, sid, 1, 200.0)
        s = (await client.get(f"/v1/reports/{sid}")).json()["data"]["summary"]
        g = s["generation"]["value"]
        assert s["generation"]["delta_percent"] == pytest.approx(
            round((g - 200.0) / 200.0 * 100, 1)
        )
