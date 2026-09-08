"""云团移动估计：相邻两帧光流 → 速度、方向、到站距离、影响时间。docs/07 §四

输入是同一 bbox 重投影后的两张灰度图，亮度作为云量代理；云像素阈值随波段不同
（可见光反照率 / 红外亮温），由调用方传入。
Farneback 稠密光流在云图上够用；块匹配精度更差，深度模型 V1 不上。
"""

import math
from dataclasses import dataclass

import cv2
import numpy as np

FRAME_MINUTES = 10
CLOUD_GRAY = 100  # 默认云像素阈（可见光反照率）；红外由调用方传更低的阈值
MIN_SPEED_KMH = 5.0  # 低于此视为静止，不做外推
MAX_IMPACT_MINUTES = 120  # 外推时效上限
CONE_DEG = 45.0  # 来向锥角：云团方向偏离站点超过 ±45° 不算逼近
MAX_SEARCH_KM = 400.0

DIRECTIONS_16 = [
    "北", "北北东", "东北", "东北东", "东", "东南东", "东南", "南南东",
    "南", "南南西", "西南", "西南西", "西", "西北西", "西北", "北北西",
]  # fmt: skip


@dataclass(frozen=True)
class CloudMotionEstimate:
    speed_kmh: float
    heading_deg: float  # 云团去向，0=北，顺时针
    covered: bool  # 站点当前已在云下
    distance_km: float | None  # 来向锥内最近云前沿；无则 None
    impact_minutes: int | None  # 超时效或不逼近为 None

    @property
    def heading_text(self) -> str:
        return bearing_text(self.heading_deg)

    @property
    def origin_text(self) -> str:
        return bearing_text((self.heading_deg + 180) % 360)

    @property
    def approaching(self) -> bool:
        return self.impact_minutes is not None


def bearing_text(deg: float) -> str:
    return DIRECTIONS_16[int(((deg % 360) + 11.25) // 22.5) % 16]


def _km_per_px(
    bbox: tuple[float, float, float, float], size: int, lat: float
) -> tuple[float, float]:
    w, s, e, n = bbox
    kx = (e - w) / size * 111.32 * math.cos(math.radians(lat))
    ky = (n - s) / size * 110.57
    return kx, ky


def estimate(
    prev_gray: np.ndarray,
    now_gray: np.ndarray,
    bbox: tuple[float, float, float, float],
    station_lat: float,
    station_lon: float,
    *,
    cloud_threshold: int = CLOUD_GRAY,
    frame_minutes: float = FRAME_MINUTES,
) -> CloudMotionEstimate | None:
    """两帧尺寸需相同。云像素太少（无云）返回 None。"""
    size = now_gray.shape[0]
    kx, ky = _km_per_px(bbox, size, station_lat)
    cloud_now = now_gray > cloud_threshold
    cloud_prev = prev_gray > cloud_threshold
    if cloud_now.sum() < size * size * 0.005:
        return None

    flow = cv2.calcOpticalFlowFarneback(
        prev_gray, now_gray, None, 0.5, 3, 21, 3, 5, 1.2, 0
    )  # (H, W, 2)，单位 px / 帧间隔，x 向东为正，y 向南为正
    both = cloud_now & cloud_prev
    if both.sum() < 20:
        both = cloud_now
    # 光流只在有纹理处可靠，大片均匀云内部估出来是 0；按梯度幅值加权
    gx = cv2.Sobel(now_gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(now_gray, cv2.CV_32F, 0, 1, ksize=3)
    weight = np.hypot(gx, gy) * both
    if weight.sum() < 1e-6:
        return None
    vx = float((flow[..., 0] * weight).sum() / weight.sum()) * kx  # km / 帧间隔
    vy = float((flow[..., 1] * weight).sum() / weight.sum()) * ky
    speed = math.hypot(vx, vy) * (60 / frame_minutes)
    heading = math.degrees(math.atan2(vx, -vy)) % 360  # 图像 y 向下，取反得北向分量

    w, s, e, n = bbox
    sx = (station_lon - w) / (e - w) * size
    sy = (n - station_lat) / (n - s) * size
    covered = bool(cloud_now[min(max(int(sy), 0), size - 1), min(max(int(sx), 0), size - 1)])

    if speed < MIN_SPEED_KMH:
        return CloudMotionEstimate(speed, heading, covered, None, None)

    # 来向锥：以站点为顶点、指向云团来处（去向的反方向）、半角 45°
    # 前沿用腐蚀过的掩膜：孤立的纹理亮点会把前沿算得偏近，导致到站时间系统性偏早
    front = cv2.erode(cloud_now.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1)
    ys, xs = np.nonzero(front)
    dx = (xs - sx) * kx
    dy = (ys - sy) * ky
    dist = np.hypot(dx, dy)
    ux, uy = -vx, -vy  # 来向单位向量（像素坐标系）
    norm = math.hypot(ux, uy)
    cos_ang = (dx * ux + dy * uy) / (dist * norm + 1e-9)
    in_cone = (cos_ang >= math.cos(math.radians(CONE_DEG))) & (dist <= MAX_SEARCH_KM)
    if covered:
        return CloudMotionEstimate(speed, heading, True, 0.0, 0)
    if not in_cone.any():
        return CloudMotionEstimate(speed, heading, False, None, None)

    d = float(dist[in_cone].min())
    minutes = int(round(d / speed * 60))
    if minutes > MAX_IMPACT_MINUTES:
        return CloudMotionEstimate(speed, heading, False, d, None)
    return CloudMotionEstimate(speed, heading, False, d, minutes)
