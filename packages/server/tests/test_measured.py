"""实测随手记与订正。docs/19 §三

要害：
- 系数拿实测去比同一个模型在同一天的回算值；样本不够、回测没改善、系数离谱都不订正。
- 在出力约束之前乘、按容量限幅；指数不乘；留档存原始值。
- 删记录能把被覆盖的累计推算值恢复回去；删电站连实测一起删。
"""

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select

from app.config import settings
from app.errors import ApiError
from app.main import app
from app.models import DailyGeneration, MeasuredEnergy, Station, StationCorrection
from app.render import tiles
from app.services import accumulate, correction, hindcast, measured, weather
from tests.fixtures_forecast import TZ, make_forecast
from tests.test_accumulate import _session
from tests.test_prediction import station

SITE = {"name": "订正站", "type": "solar", "capacity": 1000, "latitude": 31.3, "longitude": 120.62}


def _today() -> date:
    return datetime.now(ZoneInfo(TZ)).date()


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tiles, "_TILE_DIR", tmp_path / "tiles")
    weather.clear_cache()
    hindcast._cache._cache.clear()
    yield
    weather.clear_cache()
    hindcast._cache._cache.clear()


@pytest.fixture
def upstream():
    """过去 92 天的逐小时回算与线上 15 分钟预报走同一个接口，按 past_days 区分。"""
    n = settings.hindcast_max_days
    past = make_forecast(
        start_date=_midnight(_today() - timedelta(days=n)), days=n + 1, minutely=False
    )
    live = make_forecast(start_date=_midnight(_today() - timedelta(days=1)))
    calls = {"past": 0, "live": 0, "fail_past": False}

    def handler(request: httpx.Request) -> Response:
        # 回算走逐小时（past_days 按档取 7 / 31 / 92），线上预报走 15 分钟
        if "hourly" in request.url.params:
            calls["past"] += 1
            calls.setdefault("spans", []).append(int(request.url.params["past_days"]))
            assert "minutely_15" not in request.url.params
            if calls["fail_past"]:
                return Response(400, json={"reason": "测试：回算拿不到"})
            return Response(200, json=past)
        calls["live"] += 1
        return Response(200, json=live)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/static/meta\.json").mock(return_value=Response(404))
        mock.get(url__regex=r".*/v1/forecast.*").mock(side_effect=handler)
        yield calls


async def _station(client: AsyncClient, **kw) -> str:
    r = await client.post("/v1/stations", json={**SITE, **kw})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _days(n: int) -> list[str]:
    return [(_today() - timedelta(days=i)).isoformat() for i in range(1, n + 1)]


async def _record(client, sid, entries, basis="generation", status=200):
    r = await client.post(f"/v1/stations/{sid}/measured", json={"entries": entries, "basis": basis})
    assert r.status_code == status, r.text
    return r.json()


async def _model_values(client, sid, days) -> dict[str, float]:
    """先随便记一个数，拿到每天的模型同期电量，再按它记真实值。"""
    s = (await _record(client, sid, [{"kind": "day", "date": d, "kwh": 1.0} for d in days]))["data"]
    return {e["date"]: e["model_kwh"] for e in s["entries"]}


async def _record_ratio(client, sid, days, ratio: float) -> dict:
    models = await _model_values(client, sid, days)
    entries = [{"kind": "day", "date": d, "kwh": round(models[d] * ratio, 2)} for d in days]
    return (await _record(client, sid, entries))["data"]


# ─────────────────────────── 拟合（纯函数） ───────────────────────────


def _entry(i: int, day: date, kwh: float, model: float | None, kind: str = "day") -> MeasuredEnergy:
    e = MeasuredEnergy(
        station_id="s",
        kind=kind,
        period_start=day,
        period_end=day,
        kwh=kwh,
        basis="generation",
        model_kwh=model,
    )
    e.id = i
    return e


class TestFit:
    def test_样本不够时不订正并说明还差几条(self):
        today = _today()
        es = [_entry(i, today - timedelta(days=i), 80, 100) for i in range(1, 4)]
        f = correction.fit(es, today)
        assert f.k is None and not f.applied and f.method is None
        assert "再记 4 天" in f.reason

    def test_一致偏高时拟出系数并通过回测(self):
        today = _today()
        es = [_entry(i, today - timedelta(days=i), 80, 100) for i in range(1, 11)]
        f = correction.fit(es, today)
        assert f.method == "day" and f.applied
        assert f.k == pytest.approx(0.8)
        assert f.error_before == pytest.approx(25.0) and f.error_after == pytest.approx(0.0)
        assert f.sample_count == 10 and f.used_ids == frozenset(range(1, 11))

    def test_比值出界的判为停机或录错_不进拟合(self):
        today = _today()
        es = [_entry(i, today - timedelta(days=i), 80, 100) for i in range(1, 9)]
        es.append(_entry(99, today - timedelta(days=9), 3, 100))  # 停机那天
        f = correction.fit(es, today)
        assert f.k == pytest.approx(0.8) and f.excluded_count == 1
        assert 99 in f.excluded_ids and 99 not in f.used_ids

    def test_系数离谱时不订正_提示先核参数(self):
        today = _today()
        es = [_entry(i, today - timedelta(days=i), 30, 100) for i in range(1, 9)]
        f = correction.fit(es, today)
        assert f.k == pytest.approx(0.3) and not f.applied
        assert "核对装机容量" in f.reason

    def test_订正帮不上忙时不订正(self):
        today = _today()
        # 实测在模型上下来回跳、没有一致的偏向：乘一个系数修不好一般的一天
        es = [_entry(i, today - timedelta(days=i), 80 if i % 2 else 125, 100) for i in range(1, 11)]
        f = correction.fit(es, today)
        assert not f.applied and "没有明显变小" in f.reason
        assert f.error_after > f.error_before - settings.correction_min_gain_pct

    def test_一天异常不该否决整站订正(self):
        """真实截图里的情形：十天都偏低 17%，只有最近一天偏高 8%。
        旧规则只拿最近两天回测，恰好碰上这一天就把整站订正否决了。"""
        today = _today()
        es = [_entry(i, today - timedelta(days=i), 83, 100) for i in range(2, 12)]
        es.append(_entry(1, today - timedelta(days=1), 108.5, 100))
        f = correction.fit(es, today)
        assert f.applied
        assert f.error_after < f.error_before

    def test_日电量不够时用月电量(self):
        today = _today()
        es = [_entry(1, today - timedelta(days=1), 80, 100)]
        for i, back in enumerate((40, 70), start=10):
            d = today - timedelta(days=back)
            es.append(_entry(i, d, 2400, 3000, kind="month"))
        f = correction.fit(es, today)
        assert f.method == "month" and f.k == pytest.approx(0.8) and f.applied

    def test_日电量只看最近窗口(self, monkeypatch):
        monkeypatch.setattr(settings, "correction_window_days", 20)
        today = _today()
        old = [_entry(i, today - timedelta(days=30 + i), 50, 100) for i in range(1, 9)]
        f = correction.fit(old, today)
        assert f.k is None  # 全在窗口外

    def test_没有模型值的记录不算进去(self):
        today = _today()
        es = [_entry(i, today - timedelta(days=i), 80, None) for i in range(1, 9)]
        f = correction.fit(es, today)
        assert f.k is None and "还在回算" in f.reason


# ─────────────────────────── 订正怎么乘 ───────────────────────────


class TestApply:
    def _snap(self, st, days_back=1):
        fc = weather.parse_forecast(
            make_forecast(start_date=_midnight(_today() - timedelta(days=days_back)))
        )
        from app.services import energy

        return fc, energy.compute(st, fc)

    def test_系数为一时不变(self):
        st = station("solar")
        fc, raw = self._snap(st)
        same = correction.apply_snapshot(st, fc, raw, 1.0)
        assert same.daily_kwh == pytest.approx(raw.daily_kwh, rel=1e-9)
        assert same.index is raw.index

    def test_按比例缩放电量与当前功率_指数不动(self):
        st = station("solar")
        fc, raw = self._snap(st)
        half = correction.apply_snapshot(st, fc, raw, 0.5)
        assert half.daily_kwh == pytest.approx(raw.daily_kwh * 0.5, rel=1e-9)
        if raw.current_kw is not None:
            assert half.current_kw == pytest.approx(raw.current_kw * 0.5)
        assert half.index.score == raw.index.score

    def test_放大时按装机容量限幅(self):
        st = station("solar")
        fc, raw = self._snap(st)
        big = correction.apply_snapshot(st, fc, raw, 2.5)
        assert (big.hourly_kw.dropna() <= st.capacity_kw + 1e-9).all()
        assert big.daily_kwh < raw.daily_kwh * 2.5

    def test_在出力约束之前乘_上网跟着重算(self):
        st = station("solar")
        st.curtailment = {"mode": "ratio", "ratio_percent": 20}
        fc, raw = self._snap(st)
        fixed = correction.apply_snapshot(st, fc, raw, 0.5)
        assert fixed.grid_kwh == pytest.approx(fixed.daily_kwh * 0.8, rel=1e-9)
        assert fixed.grid_kwh == pytest.approx(raw.grid_kwh * 0.5, rel=1e-9)


# ─────────────────────────── 回算模型同期电量 ───────────────────────────


class TestHindcast:
    def _fc(self):
        n = settings.hindcast_max_days
        raw = make_forecast(
            start_date=_midnight(_today() - timedelta(days=n)), days=n + 1, minutely=False
        )
        return weather.parse_forecast(raw, model="best_match")

    def test_过去的日子能算_今天和太早的不算(self):
        st, fc = station("solar"), self._fc()
        assert hindcast.day_kwh(st, fc, _today() - timedelta(days=1)) > 0
        assert hindcast.day_kwh(st, fc, _today()) is None
        assert (
            hindcast.day_kwh(st, fc, _today() - timedelta(days=settings.hindcast_max_days + 1))
            is None
        )

    def test_设了出力约束取预计上网(self):
        fc = self._fc()
        day = _today() - timedelta(days=2)
        free = station("solar")
        capped = station("solar")
        capped.curtailment = {"mode": "ratio", "ratio_percent": 50}
        # 两边各自取两位小数，比较时容许 0.01 kWh
        assert hindcast.day_kwh(capped, fc, day) == pytest.approx(
            hindcast.day_kwh(free, fc, day) * 0.5, abs=0.01
        )

    def test_一个月里有一天算不出整月就不给(self):
        st, fc = station("solar"), self._fc()
        start = _today() - timedelta(days=5)
        assert hindcast.period_kwh(st, fc, start, _today() - timedelta(days=1)) is not None
        assert hindcast.period_kwh(st, fc, start, _today()) is None

    def test_按回溯天数分档拉取(self):
        assert [hindcast.span_for(d) for d in (1, 7, 8, 31, 32, 92, 200)] == [
            7,
            7,
            31,
            31,
            92,
            92,
            92,
        ]

    def test_参数变了指纹就变(self):
        st = station("solar")
        a = hindcast.digest(st)
        st.capacity_kw = 2000
        assert hindcast.digest(st) != a


# ─────────────────────────── 接口 ───────────────────────────


class TestApi:
    async def test_记满七天且一致偏高_订正生效(self, client: AsyncClient, upstream):
        sid = await _station(client)
        s = await _record_ratio(client, sid, _days(8), 0.8)
        c = s["correction"]
        assert c["enabled"] and c["applied"] and c["method"] == "day"
        assert c["k"] == pytest.approx(0.8, abs=1e-3)
        assert c["error_before"] == pytest.approx(25.0, abs=0.2)
        assert abs(c["error_after"]) < 0.5
        assert {e["status"] for e in s["entries"]} == {"used"}
        # 记最近 8 天只拉 31 天那一档；同一档当天共用，第二次记录不再出网
        assert upstream["spans"] == [31]

    async def test_七天预测按系数订正_指数不动_留档存原始值(self, client: AsyncClient, upstream):
        sid = await _station(client)
        await _record_ratio(client, sid, _days(8), 0.8)
        params = {"station_id": sid, "days": 2, "weather_model": "icon_global"}
        on = (await client.get("/v1/predictions/station", params=params)).json()["data"]
        assert on["correction"]["k"] == pytest.approx(0.8, abs=1e-3)
        assert on["days"][0]["corrected"] is True
        assert any("实测订正" in a for a in on["assumptions"])

        r = await client.patch(f"/v1/stations/{sid}", json={"correction_enabled": False})
        assert r.status_code == 200 and r.json()["data"]["correction_enabled"] is False
        off = (await client.get("/v1/predictions/station", params=params)).json()["data"]
        assert off["correction"] is None and off["days"][0]["corrected"] is False
        for a, b in zip(on["days"], off["days"], strict=True):
            assert a["energy_kwh"] == pytest.approx(
                b["energy_kwh"] * on["correction"]["k"], rel=1e-3
            )
            assert a["index_score"] == b["index_score"]

        # 订正开着的那次请求落的留档，存的是未订正的原始值
        metas = list((tiles.tile_dir().parent / "prediction-outlook").rglob("*.meta.json"))
        import json

        stored = [json.loads(m.read_text()) for m in metas]
        icon = [m for m in stored if m["model"] == "icon_global"]
        assert icon and icon[0]["days"][0]["energy_kwh"] == pytest.approx(
            off["days"][0]["energy_kwh"]
        )

    async def test_首页今日与当前功率跟着订正(self, client: AsyncClient, upstream):
        sid = await _station(client)
        await _record_ratio(client, sid, _days(8), 0.8)
        on = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]
        await client.patch(f"/v1/stations/{sid}", json={"correction_enabled": False})
        off = (await client.get("/v1/home", params={"station_id": sid})).json()["data"]
        assert on["prediction"]["corrected"] is True and off["prediction"]["corrected"] is False
        assert on["prediction"]["energy_kwh"] == pytest.approx(
            off["prediction"]["energy_kwh"] * 0.8, rel=1e-3
        )
        assert on["index"]["score"] == off["index"]["score"]

    async def test_三模式下每家先修再聚合(self, client: AsyncClient, upstream):
        sid = await _station(client)
        await _record_ratio(client, sid, _days(8), 0.8)
        d = (
            await client.get("/v1/predictions/station", params={"station_id": sid, "days": 2})
        ).json()["data"]
        assert d["model"] == "ensemble" and d["correction"] is not None
        day = d["days"][0]
        assert day["corrected"] is True
        assert day["energy_kwh_low"] <= day["energy_kwh"] <= day["energy_kwh_high"]

    @pytest.mark.parametrize(
        "entry,message",
        [
            ({"kind": "day", "date": "TODAY", "kwh": 10}, "还没过完"),
            ({"kind": "month", "date": "THIS_MONTH", "kwh": 10}, "还没过完"),
            ({"kind": "day", "date": "TOO_OLD", "kwh": 10}, "最近"),
            ({"kind": "day", "date": "YESTERDAY", "kwh": 10**9}, "满发"),
            ({"kind": "day", "date": "2026-13-40", "kwh": 10}, "格式"),
        ],
    )
    async def test_校验(self, client: AsyncClient, upstream, entry, message):
        sid = await _station(client)
        today = _today()
        fill = {
            "TODAY": today.isoformat(),
            "THIS_MONTH": today.isoformat()[:7],
            "TOO_OLD": (today - timedelta(days=settings.hindcast_max_days + 5)).isoformat(),
            "YESTERDAY": (today - timedelta(days=1)).isoformat(),
        }
        entry = {**entry, "date": fill.get(entry["date"], entry["date"])}
        body = await _record(client, sid, [entry], status=400)
        assert message in body["error"]["message"]

    async def test_删除记录恢复被覆盖的累计推算值(self, client: AsyncClient, upstream):
        sid = await _station(client)
        y = _today() - timedelta(days=1)
        db, gen = await _session()
        try:
            db.add(DailyGeneration(station_id=sid, day=y, kwh=123.0, source="forecast"))
            await db.commit()
        finally:
            await gen.aclose()
        s = (await _record(client, sid, [{"kind": "day", "date": y.isoformat(), "kwh": 100.0}]))[
            "data"
        ]
        entry_id = s["entries"][0]["id"]

        async def row():
            db, gen = await _session()
            try:
                return (
                    await db.execute(
                        select(DailyGeneration).where(
                            DailyGeneration.station_id == sid, DailyGeneration.day == y
                        )
                    )
                ).scalar_one_or_none()
            finally:
                await gen.aclose()

        r = await row()
        assert r.source == "measured" and r.kwh == 100.0
        s = (await client.delete(f"/v1/stations/{sid}/measured/{entry_id}")).json()["data"]
        assert s["entries"] == []
        r = await row()
        assert r.source == "forecast" and r.kwh == 123.0

    async def test_原来没有累计行的_删除后整行删掉(self, client: AsyncClient, upstream):
        sid = await _station(client)
        y = (_today() - timedelta(days=2)).isoformat()
        s = (await _record(client, sid, [{"kind": "day", "date": y, "kwh": 50.0}]))["data"]
        await client.delete(f"/v1/stations/{sid}/measured/{s['entries'][0]['id']}")
        db, gen = await _session()
        try:
            rows = (
                (await db.execute(select(DailyGeneration).where(DailyGeneration.station_id == sid)))
                .scalars()
                .all()
            )
            assert rows == []
        finally:
            await gen.aclose()

    async def test_月电量不写累计表(self, client: AsyncClient, upstream):
        sid = await _station(client)
        last = _today().replace(day=1) - timedelta(days=1)
        await _record(client, sid, [{"kind": "month", "date": last.isoformat()[:7], "kwh": 1000.0}])
        db, gen = await _session()
        try:
            rows = (
                (await db.execute(select(DailyGeneration).where(DailyGeneration.station_id == sid)))
                .scalars()
                .all()
            )
            assert rows == []
        finally:
            await gen.aclose()

    async def test_回算拿不到时先存记录_标待回算(self, client: AsyncClient, upstream):
        upstream["fail_past"] = True
        sid = await _station(client)
        s = (
            await _record(client, sid, [{"kind": "day", "date": d, "kwh": 80.0} for d in _days(3)])
        )["data"]
        assert len(s["entries"]) == 3
        assert {e["status"] for e in s["entries"]} == {"pending"}
        assert "还在回算" in s["correction"]["reason"]

    async def test_回算慢时先回回算中_后台跑完后能看到(
        self, client: AsyncClient, upstream, monkeypatch
    ):
        """小程序请求 10 秒就超时：回算超过等待上限就先返回，回算在后台跑完。"""
        monkeypatch.setattr(settings, "measured_refresh_budget_s", 0.2)
        real = hindcast.periods_kwh

        async def slow(*args, **kwargs):
            await asyncio.sleep(0.6)
            return await real(*args, **kwargs)

        monkeypatch.setattr(hindcast, "periods_kwh", slow)
        sid = await _station(client)
        s = (
            await _record(client, sid, [{"kind": "day", "date": d, "kwh": 80.0} for d in _days(3)])
        )["data"]
        assert {e["status"] for e in s["entries"]} == {"pending"}
        await asyncio.sleep(1.0)
        later = (await client.get(f"/v1/stations/{sid}/measured")).json()["data"]
        assert all(e["model_kwh"] is not None for e in later["entries"])

    async def test_改了容量后每日任务按新参数重算模型值(self, client: AsyncClient, upstream):
        sid = await _station(client)
        before = await _record_ratio(client, sid, _days(8), 0.8)
        assert before["correction"]["k"] == pytest.approx(0.8, abs=1e-3)
        await client.patch(f"/v1/stations/{sid}", json={"capacity": 2000})
        db, gen = await _session()
        try:
            assert await measured.refresh_all(db, app.state.http) == 1
        finally:
            await gen.aclose()
        after = (await client.get(f"/v1/stations/{sid}/measured")).json()["data"]
        # 容量翻倍、实测不变：模型值约翻倍，系数约减半
        assert after["correction"]["k"] == pytest.approx(0.4, rel=0.05)

    async def test_删电站连实测与系数一起删(self, client: AsyncClient, upstream):
        sid = await _station(client)
        await _record_ratio(client, sid, _days(8), 0.8)
        assert (await client.delete(f"/v1/stations/{sid}")).status_code == 204
        db, gen = await _session()
        try:
            assert (await db.execute(select(MeasuredEnergy))).scalars().all() == []
            assert (await db.execute(select(StationCorrection))).scalars().all() == []
        finally:
            await gen.aclose()

    async def test_累积任务按系数订正(self, client: AsyncClient, upstream):
        sid = await _station(client)
        db, gen = await _session()
        try:
            st = await db.get(Station, sid)
            sem = asyncio.Semaphore(1)
            raw = await accumulate.compute_station(app.state.http, st, sem)
            fixed = await accumulate.compute_station(app.state.http, st, sem, 0.8)
        finally:
            await gen.aclose()
        assert fixed.kwh == pytest.approx(raw.kwh * 0.8, abs=0.2)

    def test_公开目录电站不能记(self):
        st = station("solar")
        st.owner_id = "__catalog__"
        with pytest.raises(ApiError) as e:
            measured.require_own(st)
        assert e.value.status == 403
