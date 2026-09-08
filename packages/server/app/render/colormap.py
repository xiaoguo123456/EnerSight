"""图层色阶。图例由接口下发，客户端不硬编码。docs/04 §四、docs/06 §7.2"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Scale:
    title: str
    unit: str | None
    stops: list[float]  # 数值刻度
    colors: list[str]  # 与 stops 一一对应的 hex
    labels: tuple[str, str] | None = None  # 无量纲图层用

    def rgba(self, values: np.ndarray) -> np.ndarray:
        """按 stops 线性插值到 RGBA (H, W, 4)，NaN 透明。"""
        rgb = np.array([_hex(c) for c in self.colors], dtype=float)  # (n, 3)
        stops = np.array(self.stops, dtype=float)
        v = np.clip(values, stops[0], stops[-1])
        out = np.empty(values.shape + (4,), dtype=np.uint8)
        for ch in range(3):
            out[..., ch] = np.interp(v, stops, rgb[:, ch]).astype(np.uint8)
        out[..., 3] = np.where(np.isnan(values), 0, 190).astype(np.uint8)  # 0.75 透明度
        return out


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


SCALES: dict[str, Scale] = {
    "radiation": Scale(
        title="辐射强度（W/m²）",
        unit="W/m²",
        stops=[0, 200, 400, 600, 800, 1000],
        colors=["#3b5bdb", "#22b8cf", "#51cf66", "#fcc419", "#ff922b", "#f03e3e"],
    ),
    "temperature": Scale(
        title="温度（℃）",
        unit="℃",
        stops=[-20, -10, 0, 10, 20, 30, 40],
        colors=["#4c6ef5", "#339af0", "#22b8cf", "#51cf66", "#fcc419", "#ff922b", "#f76707"],
    ),
    "wind": Scale(
        title="风速（m/s）",
        unit="m/s",
        stops=[0, 5, 10, 15, 20],
        colors=["#e7f5ff", "#74c0fc", "#4c6ef5", "#7048e8", "#f03e3e"],
    ),
    "cloud": Scale(
        title="云量强度",
        unit=None,
        stops=[0, 50, 100],
        colors=["#1f2937", "#6b7280", "#ffffff"],
        labels=("低", "高"),
    ),
}

# 图层 → Open-Meteo 字段
FIELD: dict[str, str] = {
    "radiation": "shortwave_radiation",
    "temperature": "temperature_2m",
    "wind": "wind_speed_10m",
    "cloud": "cloud_cover",
}
