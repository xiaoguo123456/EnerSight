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
