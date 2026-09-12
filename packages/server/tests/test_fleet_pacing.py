"""全目录汇总的坐标限速。

Open-Meteo 的 600 次/分钟是按坐标计的 —— 实测一分钟内推到第 600 个坐标就 429，
与分成几次请求无关。所以批次大小只省 HTTP 往返，真正要控的是坐标速率。
"""

import time

import pytest

from app.config import settings
from app.services.fleet_prediction import Pacer

HARD_LIMIT = 600  # Open-Meteo 免费层 600 次/分钟，按坐标计


def _max_in_window(marks: list[tuple[float, int]], window: float = 60.0) -> int:
    """任意 window 秒滑动窗口内发出的坐标数上限。"""
    worst = 0
    for t0, _ in marks:
        worst = max(worst, sum(n for t, n in marks if t0 <= t < t0 + window))
    return worst


class TestPacer:
    async def test_首个批次不等待(self):
        p = Pacer(480)
        t0 = time.monotonic()
        await p.take(100)
        assert time.monotonic() - t0 < 0.05

    async def test_后续批次按坐标数等待(self):
        p = Pacer(6000)  # 每坐标 10ms，测试里跑得快
        await p.take(10)
        t0 = time.monotonic()
        await p.take(10)
        assert 0.08 < time.monotonic() - t0 < 0.3

    async def test_滑动窗口内不超过硬上限(self):
        """按配置速率排出一小时的发送时刻，检查任意一分钟窗口都在 600 以内。"""
        per_request = settings.fleet_coords_per_request
        cost = 60.0 / settings.fleet_coords_per_minute
        marks = []
        nxt = 0.0
        for _ in range(200):  # 200 x per_request 个坐标，足够铺满多个窗口
            marks.append((nxt, per_request))
            nxt += per_request * cost
        assert _max_in_window(marks) <= HARD_LIMIT, (
            f"一分钟内最多发出 {_max_in_window(marks)} 个坐标，超过 {HARD_LIMIT}"
        )

    async def test_配置留了余量给别的调用(self):
        """限额按 IP 算，站点预报与元数据共用同一份 600/分钟。"""
        assert settings.fleet_coords_per_minute < HARD_LIMIT

    @pytest.mark.parametrize("coords", [741, 7748])
    async def test_整轮耗时在后台任务可接受范围内(self, coords: int):
        """1° 的 741 格与 0.1° 的 7748 格：限速后要多久。每天只跑两轮，分钟级可接受。"""
        seconds = coords * 60.0 / settings.fleet_coords_per_minute
        assert seconds < 30 * 60


class TestBatchSize:
    async def test_批次大小只影响往返次数不影响额度(self):
        """留个提醒：调大它不省配额，按坐标计费。"""
        coords = 741
        for per_request in (25, 100):
            requests = -(-coords // per_request)
            assert requests * per_request >= coords
        # 无论怎么切，付出去的坐标数不变
        assert coords == 741


class TestGridStep:
    """光伏 1°、风电 0.25°。辐射场在百公里尺度上平滑，风速不是。docs/04 §七"""

    async def test_风电网格比光伏细(self):
        from app.services.fleet_prediction import grid_step

        assert grid_step("wind") <= grid_step("solar")

    async def test_分组键带类型(self):
        """两种类型步长不同，同一坐标附近的光伏与风电属于不同的格，键不带类型会错分。"""
        from app.models import CatalogPlant
        from app.services.fleet_prediction import cell

        def at(kind: str):
            return CatalogPlant(
                id=kind,
                source="gem",
                source_id=kind,
                type=kind,
                name=kind,
                latitude=36.62,
                longitude=100.37,
                capacity_kw=1000,
                status="operating",
            )

        assert cell(at("solar"))[0] == "solar"
        assert cell(at("wind"))[0] == "wind"
        assert cell(at("solar"))[1:] != cell(at("wind"))[1:]

    async def test_格心不带浮点噪声(self):
        """0.25 这类步长容易算出 36.375000000000004，会污染缓存键与请求参数。"""
        from app.models import CatalogPlant
        from app.services.fleet_prediction import cell

        for lat in (36.62, 21.75, 43.24, -0.13):
            _, clat, clon = cell(
                CatalogPlant(
                    id="x",
                    source="gem",
                    source_id="x",
                    type="wind",
                    name="x",
                    latitude=lat,
                    longitude=lat + 60,
                    capacity_kw=1,
                    status="operating",
                )
            )
            assert len(str(clat).split(".")[-1]) <= 4
            assert len(str(clon).split(".")[-1]) <= 4

    async def test_同坐标的两类场站共用一份气象(self):
        from app.services.fleet_prediction import coord_key

        assert coord_key(36.5, 100.5) == coord_key(36.5, 100.5)
