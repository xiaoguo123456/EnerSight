from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import respx
from httpx import AsyncClient, Response

from app.ai import generate as ai
from app.ai.input import ReportInput, extract_numbers
from app.ai.schema import AIReport, ReportPeriod
from app.config import settings
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
