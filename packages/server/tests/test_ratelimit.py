import pytest
from httpx import AsyncClient

from app import ratelimit
from app.config import settings


@pytest.fixture(autouse=True)
def _limit(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    yield
    ratelimit.reset()


class TestRateLimit:
    async def test_超过阈值返回429且结构合契约(self, client: AsyncClient):
        for _ in range(3):
            assert (await client.get("/v1/stations")).status_code == 200
        r = await client.get("/v1/stations")
        assert r.status_code == 429
        assert r.json() == {
            "error": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"}
        }
        assert int(r.headers["retry-after"]) >= 1

    async def test_健康检查不限流(self, client: AsyncClient):
        for _ in range(5):
            assert (await client.get("/health")).status_code == 200

    async def test_有效token分开计数(self, client: AsyncClient):
        from app.auth import issue_token

        ta, _ = issue_token("user-a")
        tb, _ = issue_token("user-b")
        for _ in range(3):
            await client.get("/v1/stations", headers={"Authorization": f"Bearer {ta}"})
        r_a = await client.get("/v1/stations", headers={"Authorization": f"Bearer {ta}"})
        r_b = await client.get("/v1/stations", headers={"Authorization": f"Bearer {tb}"})
        assert r_a.status_code == 429
        assert r_b.status_code == 200

    async def test_无效token不能绕开按IP限流(self, client: AsyncClient):
        """随便编 Bearer 不能各开一个桶：无效 token 一律按来源 IP 计数"""
        for i in range(3):
            await client.get("/v1/stations", headers={"Authorization": f"Bearer fake-{i}"})
        r = await client.get("/v1/stations", headers={"Authorization": "Bearer fake-9"})
        assert r.status_code == 429

    def test_键数到上限时先清空闲键(self):
        for i in range(ratelimit.MAX_KEYS):
            ratelimit.check(f"idle-{i}", 5, now=0.0)
        assert ratelimit.check("busy", 5, now=100.0) == 0  # 空闲键被清，忙碌键不受影响
        assert len(ratelimit._hits) == 1

    def test_滑动窗口过期后放行(self):
        assert ratelimit.check("k", 2, now=100.0) == 0
        assert ratelimit.check("k", 2, now=101.0) == 0
        assert ratelimit.check("k", 2, now=102.0) > 0
        assert ratelimit.check("k", 2, now=161.0) == 0  # 第一条 100.0 已滑出 60s 窗口

    def test_关闭限流(self, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
        # 中间件读 settings，limit<=0 直接放行；这里只验证 check 本身不受影响
        assert ratelimit.check("k2", 1, now=0.0) == 0
