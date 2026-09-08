"""光流外推的历史回放评估：拿归档帧逐帧出预测，再对照后续帧看云到底什么时候到。docs/07 §八

命中：预测「m 分钟后到站」，实际首次云覆盖时刻与之相差 ≤ tolerance。
偏差：云来了，但时间差超过 tolerance。
虚警：预测了到站，但到 m + 60 分钟都没覆盖。
漏报：站点在 120 分钟内被云覆盖，而覆盖前 30–120 分钟窗口内没有任何一帧给出预测。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from app.satellite import archive, motion
from app.satellite.reproject import Reprojected, reproject

TOLERANCE_MIN = 20
FALSE_ALARM_GRACE_MIN = 60  # 预测到站后再等这么久还没来才算虚警
MAX_HORIZON_MIN = motion.MAX_IMPACT_MINUTES


@dataclass
class ReplayStats:
    frames: int = 0
    predictions: int = 0
    hits: int = 0
    off_by: int = 0  # 云来了但时间差超过容差
    false_alarms: int = 0
    misses: int = 0
    errors_min: list[int] = field(default_factory=list)

    @property
    def hit_rate(self) -> float | None:
        return self.hits / self.predictions if self.predictions else None

    @property
    def mae_min(self) -> float | None:
        return float(np.mean(np.abs(self.errors_min))) if self.errors_min else None


def _station_px(bbox: tuple[float, float, float, float], lat: float, lon: float, size: int):
    w, s, e, n = bbox
    sx = min(max(int((lon - w) / (e - w) * size), 0), size - 1)
    sy = min(max(int((n - lat) / (n - s) * size), 0), size - 1)
    return sy, sx


def evaluate(
    times: list[datetime],
    band: str,
    bbox: tuple[float, float, float, float],
    lat: float,
    lon: float,
    cloud_threshold: int,
    size: int = 512,
) -> ReplayStats:
    """times 需升序，同一波段。缺帧自动跳过。"""
    frames: dict[datetime, Reprojected] = {}
    for t in times:
        m = archive.load_mosaic(t, band, bbox)
        if m is not None:
            frames[t] = reproject(m, bbox, size)
    ordered = sorted(frames)
    sy, sx = _station_px(bbox, lat, lon, size)
    covered = {t: bool(frames[t].gray[sy, sx] > cloud_threshold) for t in ordered}

    def first_cover_after(t: datetime, horizon: timedelta) -> datetime | None:
        for u in ordered:
            if t < u <= t + horizon and covered[u]:
                return u
        return None

    stats = ReplayStats(frames=len(ordered))
    predicted_for: set[datetime] = set()  # 被某个预测覆盖到的实际到站时刻
    for i in range(1, len(ordered)):
        t_prev, t = ordered[i - 1], ordered[i]
        gap = (t - t_prev).total_seconds() / 60
        if gap > 25 or covered[t]:
            continue
        est = motion.estimate(
            frames[t_prev].gray,
            frames[t].gray,
            bbox,
            lat,
            lon,
            cloud_threshold=cloud_threshold,
            frame_minutes=gap,
        )
        if est is None or est.impact_minutes is None or est.impact_minutes <= 0:
            continue
        stats.predictions += 1
        m = est.impact_minutes
        actual = first_cover_after(t, timedelta(minutes=m + FALSE_ALARM_GRACE_MIN))
        if actual is None:
            stats.false_alarms += 1
            continue
        err = int(round((actual - t).total_seconds() / 60)) - m
        stats.errors_min.append(err)
        if abs(err) <= TOLERANCE_MIN:
            stats.hits += 1
            predicted_for.add(actual)
        else:
            stats.off_by += 1

    # 漏报：每个「从无云到有云」的到站事件，往前 30–120 分钟看有没有预测命中
    for i in range(1, len(ordered)):
        t = ordered[i]
        if covered[t] and not covered[ordered[i - 1]] and t not in predicted_for:
            stats.misses += 1
    return stats
