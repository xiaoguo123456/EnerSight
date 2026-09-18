"""三模式区间与预报演变。docs/19 §一、§二

两条线各自的要害：
- 区间：主数字必须**是某一家的数**，且与它自己的曲线自洽；`ensemble` 绝不能漏成上游模型名。
- 演变：同一目标日、同一模型、按起报去重，不足两份不下收敛结论。
"""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from httpx import AsyncClient, Response

from app.config import settings
from app.render import tiles
from app.schemas.common import ConvergenceLevel, SpreadLevel
from app.schemas.prediction import ForecastBasis
from app.services import ensemble, evolution, prediction, prediction_archive, weather
from tests.fixtures_forecast import TZ, make_forecast
from tests.test_prediction import station


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    weather.clear_cache()
    yield
    weather.clear_cache()


def _yesterday():
    return (datetime.now(ZoneInfo(TZ)) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )


def member(model: str, peak: float, days: int = 3) -> ensemble.Member:
    """辐射峰值不同 → 日电量不同，用来区分三家。"""
    fc = weather.parse_forecast(make_forecast(start_date=_yesterday(), peak_today=peak))
    st = station("solar")
    st.id = "集合站"
    return ensemble.Member(model, fc, prediction.compute_days(st, fc, days, model))


def energy_of(m: ensemble.Member, index: int = 0) -> float:
    return m.outlook.days[index].energy_kwh


class TestAggregate:
    def setup_method(self):
        # 800 居中；顺序故意不按大小排，确认挑的是中位而不是第几个
        self.members = [
            member("ecmwf_ifs", 600),
            member("icon_global", 1000),
            member("gfs_global", 800),
        ]
        self.out = ensemble.aggregate(self.members, [])

    def test_主数字取中位成员且与自己的曲线自洽(self):
        day = self.out.days[0]
        by_model = {m.model: energy_of(m) for m in self.members}
        assert day.median_model == "gfs_global"
        assert day.energy_kwh == by_model["gfs_global"]
        # 曲线来自同一家：求和回得去主数字。逐点中位合成就做不到这一点
        total = sum(p.value for p in day.power_kw if p.value is not None) * (
            day.resolution_minutes / 60
        )
        assert total == pytest.approx(day.energy_kwh, rel=1e-6)

    def test_区间端点各等于某一家的日电量(self):
        day = self.out.days[0]
        values = sorted(m for m in (energy_of(x) for x in self.members))
        assert day.energy_kwh_low == values[0]
        assert day.energy_kwh_high == values[-1]
        assert day.energy_kwh_low <= day.energy_kwh <= day.energy_kwh_high
        assert {m.model for m in day.member_energy_kwh} == {
            "ecmwf_ifs",
            "icon_global",
            "gfs_global",
        }

    def test_功率带是逐点包络_且主曲线落在带内(self):
        day = self.out.days[0]
        assert len(day.power_kw_low) == len(day.power_kw) == len(day.power_kw_high)
        checked = 0
        for lo, mid, hi in zip(day.power_kw_low, day.power_kw, day.power_kw_high, strict=True):
            assert lo.time == mid.time == hi.time
            if lo.value is None or mid.value is None:
                continue
            assert lo.value <= mid.value <= hi.value
            checked += 1
        assert checked > 0

    def test_分歧度按日电量算并分档(self):
        day = self.out.days[0]
        low, high, mid = day.energy_kwh_low, day.energy_kwh_high, day.energy_kwh
        assert day.spread_percent == pytest.approx(round((high - low) / mid * 100, 1))
        assert day.spread_level in set(SpreadLevel)
        assert self.out.ensemble.spread_level == day.spread_level
        assert self.out.model == "ensemble"
        assert len(self.out.ensemble.members) == 3

    def test_分档跟着配置走(self, monkeypatch):
        monkeypatch.setattr(settings, "ensemble_spread_moderate", 0.1)
        monkeypatch.setattr(settings, "ensemble_spread_high", 0.2)
        assert ensemble.aggregate(self.members, []).days[0].spread_level == SpreadLevel.STRONG
        monkeypatch.setattr(settings, "ensemble_spread_moderate", 999.0)
        assert ensemble.aggregate(self.members, []).days[0].spread_level == SpreadLevel.AGREE

    def test_两家时取偏低的一家_不假装有中位数(self):
        two = [self.members[0], self.members[1]]  # 600 与 1000
        day = ensemble.aggregate(two, ["gfs_global"]).days[0]
        assert day.median_model == "ecmwf_ifs"
        assert day.energy_kwh == energy_of(two[0])
        assert len(day.member_energy_kwh) == 2


class TestEarliestBasis:
    """三家起报不一致时对外统一报最早那一家。docs/19 §一"""

    @staticmethod
    def _with(m: ensemble.Member, issued: str | None, fetched: str) -> ensemble.Member:
        m.outlook.basis = ForecastBasis(
            model=m.model,
            resolved_model=m.model,
            issued_at=issued,
            available_at=None if issued is None else fetched,
            fetched_at=fetched,
        )
        return m

    def test_取三家中最早的起报(self):
        ms = [
            self._with(
                member("ecmwf_ifs", 600), "2026-09-18T08:00:00+08:00", "2026-09-18T15:10:00+08:00"
            ),
            self._with(
                member("icon_global", 800), "2026-09-18T14:00:00+08:00", "2026-09-18T17:40:00+08:00"
            ),
            self._with(
                member("gfs_global", 700), "2026-09-18T02:00:00+08:00", "2026-09-18T09:00:00+08:00"
            ),
        ]
        basis = ensemble.aggregate(ms, []).basis
        assert basis.issued_at == "2026-09-18T02:00:00+08:00"
        # 可用与拉取时刻跟着同一家，不拼三家
        assert basis.fetched_at == "2026-09-18T09:00:00+08:00"
        assert basis.model == "ensemble" and basis.resolved_model is None

    def test_按时刻比较而不是按字符串(self):
        """偏移不同的时刻按字符串排会排错：UTC 的 01:00 其实是北京 09:00，比 08:00 晚。"""
        ms = [
            self._with(
                member("ecmwf_ifs", 600), "2026-09-18T01:00:00+00:00", "2026-09-18T12:00:00+00:00"
            ),
            self._with(
                member("icon_global", 800), "2026-09-18T08:00:00+08:00", "2026-09-18T15:00:00+08:00"
            ),
        ]
        assert ensemble.aggregate(ms, []).basis.issued_at == "2026-09-18T08:00:00+08:00"

    def test_有一家拿不到起报就不猜_只给最早拉取时刻(self):
        ms = [
            self._with(
                member("ecmwf_ifs", 600), "2026-09-18T08:00:00+08:00", "2026-09-18T15:10:00+08:00"
            ),
            self._with(member("icon_global", 800), None, "2026-09-18T14:30:00+08:00"),
        ]
        basis = ensemble.aggregate(ms, []).basis
        assert basis.issued_at is None and basis.available_at is None
        assert basis.fetched_at == "2026-09-18T14:30:00+08:00"


class TestDegrade:
    """成员失败不能掀翻整轮：剩两家仍给区间，剩一家退回单模型。"""

    async def _compute(self, monkeypatch, available: dict[str, float]):
        made = {m: member(m, peak) for m, peak in available.items()}

        async def fake(_http, _station, _days, model, _correction=None):
            if model not in made:
                raise httpx.ConnectError("upstream down")
            return made[model]

        monkeypatch.setattr(ensemble, "_member", fake)
        return await ensemble.compute(None, station("solar"), 3)

    async def test_剩两家仍给区间并说明缺了谁(self, monkeypatch):
        out, members = await self._compute(monkeypatch, {"ecmwf_ifs": 600, "icon_global": 1000})
        assert len(members) == 2
        assert out.ensemble is not None
        assert out.days[0].energy_kwh_low < out.days[0].energy_kwh_high
        assert any("未取到 gfs_global" in a for a in out.assumptions)

    async def test_只剩一家退回单模型且不给区间(self, monkeypatch):
        out, members = await self._compute(monkeypatch, {"icon_global": 1000})
        assert len(members) == 1
        assert out.ensemble is None
        assert out.model == "icon_global"
        assert out.days[0].energy_kwh_low is None
        assert any("只用 icon_global" in a for a in out.assumptions)

    async def test_全部失败时抛出(self, monkeypatch):
        with pytest.raises(httpx.ConnectError):
            await self._compute(monkeypatch, {})


class TestRouting:
    """ensemble 是路由层的模式，绝不能当成上游模型名发出去。"""

    async def _station(self, client: AsyncClient) -> str:
        r = await client.post(
            "/v1/stations",
            json={
                "name": "集合站",
                "type": "solar",
                "capacity": 1000,
                "latitude": 31.3,
                "longitude": 120.62,
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["data"]["id"]

    @pytest.fixture
    def upstream(self):
        full = make_forecast(start_date=_yesterday())
        seen: list[str] = []

        def handler(request: httpx.Request) -> Response:
            seen.append(request.url.params.get("models", ""))
            return Response(200, json={"timezone": TZ, "minutely_15": full["minutely_15"]})

        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=r".*/static/meta\.json").mock(return_value=Response(404))
            mock.get(url__regex=r".*/v1/forecast.*").mock(side_effect=handler)
            yield seen

    async def test_预测接口默认三模式_上游只收到真实模型名(self, client: AsyncClient, upstream):
        sid = await self._station(client)
        r = await client.get("/v1/predictions/station", params={"station_id": sid, "days": 2})
        assert r.status_code == 200, r.text
        assert r.headers["X-Weather-Model"] == "ensemble"
        data = r.json()["data"]
        assert data["model"] == "ensemble"
        assert data["ensemble"] is not None
        assert len(data["days"][0]["member_energy_kwh"]) == 3
        assert "ensemble" not in upstream  # 上游只认真实模型名
        assert set(upstream) == set(ensemble.members())

    async def test_显式选单一模型时没有区间(self, client: AsyncClient, upstream):
        sid = await self._station(client)
        r = await client.get(
            "/v1/predictions/station",
            params={"station_id": sid, "days": 2, "weather_model": "icon_global"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["X-Weather-Model"] == "icon_global"
        data = r.json()["data"]
        assert data["ensemble"] is None
        assert data["days"][0]["energy_kwh_low"] is None
        assert set(upstream) == {"icon_global"}

    async def test_其他接口不受三模式影响(self, client: AsyncClient, upstream):
        sid = await self._station(client)
        r = await client.get("/v1/home", params={"station_id": sid})
        assert r.status_code == 200, r.text
        # 首页只是 outlook 加载前的占位，不该为了三家拖慢首屏
        assert r.headers["X-Weather-Model"] == "best_match"
        assert set(upstream) == {"best_match"}

    async def test_把三模式传给别的接口会退回自动(self, client: AsyncClient, upstream):
        sid = await self._station(client)
        r = await client.get("/v1/home", params={"station_id": sid, "weather_model": "ensemble"})
        assert r.status_code == 200, r.text
        assert r.headers["X-Weather-Model"] == "best_match"
        assert "ensemble" not in upstream

    async def test_查询参数优先于请求头_即小程序的真实请求形态(self, client: AsyncClient, upstream):
        """小程序请求头只带真实模型名（后端未部署新版时也不会 400），
        三模式只在 7 天预测的查询参数里带。"""
        sid = await self._station(client)
        r = await client.get(
            "/v1/predictions/station",
            params={"station_id": sid, "days": 2, "weather_model": "ensemble"},
            headers={"X-Weather-Model": "best_match"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["X-Weather-Model"] == "ensemble"
        assert r.json()["data"]["ensemble"] is not None

    async def test_未知模型仍然拒绝(self, client: AsyncClient):
        r = await client.get(
            "/v1/predictions/station", params={"station_id": "x", "weather_model": "era5"}
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_MODEL"


class TestEvolution:
    """演变读的是留档索引，不重算，也不跨模型混。"""

    def _issue(self, st, target, issued_at: str | None, peak: float, model="ecmwf_ifs"):
        fc = weather.parse_forecast(make_forecast(start_date=_yesterday(), peak_today=peak))
        out = prediction.compute_days(st, fc, 3, model)
        out.basis = ForecastBasis(
            model=model,
            resolved_model=model,
            issued_at=issued_at,
            available_at=None,
            fetched_at=issued_at or datetime.now(UTC).isoformat(),
        )
        prediction_archive.save_outlook(st, fc, out)
        return out

    def _station(self):
        st = station("solar")
        st.id = "演变站"
        return st

    def _target(self):
        return datetime.now(ZoneInfo(TZ)).date()

    def test_历次起报按时间排开并给出收敛度(self):
        st, target = self._station(), self._target()
        base = datetime.now(UTC).replace(microsecond=0)
        for k, peak in enumerate([600.0, 780.0, 800.0]):
            self._issue(st, target, (base - timedelta(hours=18 - 6 * k)).isoformat(), peak)
        ev = evolution.read(st.id, target)
        assert len(ev.issuances) == 3
        assert [i.issued_at for i in ev.issuances] == sorted(i.issued_at for i in ev.issuances)
        assert ev.issuances[0].energy_kwh < ev.issuances[-1].energy_kwh
        assert ev.model == "ecmwf_ifs"
        assert ev.convergence in set(ConvergenceLevel)
        assert ev.range_percent is not None

    def test_同一起报多次签发只留最后一份(self):
        st, target = self._station(), self._target()
        issued = datetime.now(UTC).replace(microsecond=0).isoformat()
        first = self._issue(st, target, issued, 600.0)
        # 同一批起报、设备也没变，但过一会儿又算了一遍
        second = self._issue(st, target, issued, 900.0)
        assert first.days[0].energy_kwh != second.days[0].energy_kwh
        ev = evolution.read(st.id, target)
        assert len(ev.issuances) == 1
        assert ev.convergence is None and ev.range_percent is None  # 不足两份不下结论

    def test_不跨模型混(self):
        st, target = self._station(), self._target()
        base = datetime.now(UTC).replace(microsecond=0)
        self._issue(st, target, base.isoformat(), 600.0, "ecmwf_ifs")
        self._issue(st, target, (base - timedelta(hours=6)).isoformat(), 900.0, "icon_global")
        assert len(evolution.read(st.id, target).issuances) == 1
        assert len(evolution.read(st.id, target, "icon_global").issuances) == 1

    def test_起报拿不到时按拉取时段归并且不冒充起报(self):
        st, target = self._station(), self._target()
        for peak in (600.0, 700.0):
            fc = weather.parse_forecast(make_forecast(start_date=_yesterday(), peak_today=peak))
            out = prediction.compute_days(st, fc, 3, "ecmwf_ifs")
            assert out.basis.issued_at is None  # 合成响应没有元数据
            prediction_archive.save_outlook(st, fc, out)
        ev = evolution.read(st.id, target)
        assert len(ev.issuances) == 1  # 同一个 6 小时片，归并成一条
        assert ev.issuances[0].issued_at is None

    def test_收敛度分档跟着配置走(self, monkeypatch):
        st, target = self._station(), self._target()
        base = datetime.now(UTC).replace(microsecond=0)
        for k, peak in enumerate([600.0, 1000.0]):
            self._issue(st, target, (base - timedelta(hours=6 - 6 * k)).isoformat(), peak)
        monkeypatch.setattr(settings, "evolution_stable_pct", 99.0)
        assert evolution.read(st.id, target).convergence == ConvergenceLevel.STABLE
        monkeypatch.setattr(settings, "evolution_stable_pct", 0.1)
        monkeypatch.setattr(settings, "evolution_swing_pct", 0.2)
        assert evolution.read(st.id, target).convergence == ConvergenceLevel.SWING

    def test_只看最近几份起报(self, monkeypatch):
        st, target = self._station(), self._target()
        base = datetime.now(UTC).replace(microsecond=0)
        # 最早那份差得远，但只要不在窗口内就不该影响收敛结论
        for k, peak in enumerate([200.0, 800.0, 810.0, 805.0]):
            self._issue(st, target, (base - timedelta(hours=24 - 6 * k)).isoformat(), peak)
        monkeypatch.setattr(settings, "evolution_issuances", 3)
        assert evolution.read(st.id, target).convergence == ConvergenceLevel.STABLE
        monkeypatch.setattr(settings, "evolution_issuances", 4)
        assert evolution.read(st.id, target).convergence == ConvergenceLevel.SWING

    def test_目标日没有留档时为空(self):
        st, target = self._station(), self._target()
        self._issue(st, target, datetime.now(UTC).isoformat(), 600.0)
        ev = evolution.read(st.id, target + timedelta(days=30))
        assert ev.issuances == [] and ev.convergence is None

    async def test_接口只读留档不触发计算(self, client: AsyncClient):
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=r".*").mock(side_effect=AssertionError("不该出网"))
            r = await client.get(
                "/v1/predictions/station/history",
                params={"station_id": "不存在的站", "date": self._target().isoformat()},
            )
        assert r.status_code == 404


class TestPrune:
    def test_按保留天数清理留档目录(self, tmp_path, monkeypatch):
        st = station("solar")
        st.id = "清理站"
        fc = weather.parse_forecast(make_forecast(start_date=_yesterday()))
        out = prediction.compute_days(st, fc, 2, "ecmwf_ifs")
        old = (datetime.now().date() - timedelta(days=200)).isoformat()
        out.generated_at = f"{old}T00:00:00+00:00"
        prediction_archive.save_outlook(st, fc, out)
        fresh = prediction.compute_days(st, fc, 2, "icon_global")
        prediction_archive.save_outlook(st, fc, fresh)
        root = tiles.tile_dir().parent / "prediction-outlook"
        assert {p.name for p in root.iterdir()} >= {old}
        assert evolution.prune() >= 1
        assert old not in {p.name for p in root.iterdir()}
        assert list(root.iterdir())  # 保留期内的不动

    def test_删站时索引也被清掉(self, tmp_path):
        st = station("solar")
        st.id = "待删站"
        fc = weather.parse_forecast(make_forecast(start_date=_yesterday()))
        prediction_archive.save_outlook(st, fc, prediction.compute_days(st, fc, 2, "ecmwf_ifs"))
        root = tiles.tile_dir().parent / "prediction-outlook"
        assert list(root.rglob("*.meta.json"))
        prediction_archive.purge_station_archives({"待删站"})
        assert not list(root.rglob("*.json"))


class TestReportStability:
    """AI 报告只拿到稳定度档位文字，历次起报的数字一个都不能进校验集。docs/08 §5.1"""

    def _issue(self, st, issued_at: str, peak: float):
        fc = weather.parse_forecast(make_forecast(start_date=_yesterday(), peak_today=peak))
        out = prediction.compute_days(st, fc, 2, "ecmwf_ifs")
        out.basis = ForecastBasis(
            model="ecmwf_ifs",
            resolved_model="ecmwf_ifs",
            issued_at=issued_at,
            available_at=None,
            fetched_at=issued_at,
        )
        prediction_archive.save_outlook(st, fc, out)
        return out.days[0].energy_kwh

    def test_摇摆时报告输入带档位文字但不带起报数值(self):
        from app.ai.input import ReportInput

        st = station("solar")
        st.id = "报告站"
        base = datetime.now(UTC).replace(microsecond=0)
        values = [
            self._issue(st, (base - timedelta(hours=12 - 6 * k)).isoformat(), peak)
            for k, peak in enumerate([300.0, 900.0, 500.0])
        ]
        target = datetime.now(ZoneInfo(TZ)).date()
        text = evolution.stability_text(st.id, target)
        assert text == evolution.STABILITY_TEXT[ConvergenceLevel.SWING]
        inp = ReportInput(
            station_name="报告站",
            station_type="solar",
            capacity_kw=1000,
            address=None,
            score=60,
            level="fair",
            attribution=[],
            periods=[],
            alert=None,
            daily_kwh=1234,
            equivalent_hours=1.2,
            stability=text,
        )
        rendered = inp.render()
        assert f"预报稳定度：{text}" in rendered
        for v in values:
            assert f"{v:,.0f}" not in rendered and f"{v:.0f}" not in rendered

    @staticmethod
    def _base(**kw):
        periods = [
            {
                "key": key,
                "label": label,
                "range": "",
                "weather": "晴",
                "avg_radiation": 500.0,
                "avg_cloud": 20.0,
            }
            for key, label in (("morning", "上午"), ("afternoon", "下午"), ("evening", "晚间"))
        ]
        return (
            dict(
                station_name="站",
                station_type="solar",
                capacity_kw=1000,
                address=None,
                score=80,
                level="good",
                attribution=[],
                periods=periods,
                alert=None,
                daily_kwh=1000,
                equivalent_hours=1.0,
            )
            | kw
        )

    def test_规则模板在摇摆时先提醒临近再看(self):
        from app.ai.input import ReportInput
        from app.ai.rule_provider import render

        swing_text = evolution.STABILITY_TEXT[ConvergenceLevel.SWING]
        swing = render(ReportInput(**self._base(), stability=swing_text))
        assert "来回摇摆" in swing.suggestions[0]
        stable_text = evolution.STABILITY_TEXT[ConvergenceLevel.STABLE]
        stable = render(ReportInput(**self._base(), stability=stable_text))
        assert not any("摇摆" in s for s in stable.suggestions)
        assert render(ReportInput(**self._base())).suggestions == stable.suggestions

    def test_有预警又摇摆时建议仍不超过三条(self):
        """有预警时建议本来就是三条，再插一条会过不了 AIReport 的校验，整份报告生成失败。"""
        from app.ai.input import ReportInput
        from app.ai.rule_provider import render
        from app.schemas.home import AlertSummary

        alert = AlertSummary(
            id="a1",
            level="moderate",
            title="云团逼近",
            description="约 45 分钟后影响",
            published_at=datetime.now(UTC).isoformat(),
            station_id="站",
            source="satellite",
        )
        out = render(
            ReportInput(
                **self._base(alert=alert),
                stability=evolution.STABILITY_TEXT[ConvergenceLevel.SWING],
            )
        )
        assert len(out.suggestions) == 3
        assert "来回摇摆" in out.suggestions[0]

    def test_没有历史时不出这一行(self):
        target = datetime.now(ZoneInfo(TZ)).date()
        assert evolution.stability_text("没留过档的站", target) is None


def test_留档索引不含气象输入(tmp_path):
    """索引就是为了不用解析整份气象；真带进去就白建了。"""
    st = station("solar")
    st.id = "索引站"
    fc = weather.parse_forecast(make_forecast(start_date=_yesterday()))
    prediction_archive.save_outlook(st, fc, prediction.compute_days(st, fc, 3, "ecmwf_ifs"))
    meta = next(iter((tiles.tile_dir().parent / "prediction-outlook").rglob("*.meta.json")))
    row = json.loads(meta.read_text())
    assert "weather_input" not in row
    assert set(row) == {"station_id", "model", "generated_at", "basis", "days"}
