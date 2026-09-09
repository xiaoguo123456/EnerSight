"""AI 输入：只给算好的结论，不给原始数据流。docs/08 §5.1

模型不做任何算术。数值一致性校验依赖这里列出的数字集合。
"""

from dataclasses import dataclass, field

import pandas as pd

from app.metrics.index import IndexResult
from app.models import Station
from app.schemas.home import AlertSummary
from app.services.weather import Forecast
from app.services.weather_text import describe

PERIODS = [("morning", "上午", 6, 12), ("afternoon", "下午", 12, 18), ("evening", "晚间", 18, 24)]


@dataclass
class ReportInput:
    station_name: str
    station_type: str
    capacity_kw: float
    address: str | None
    score: float
    level: str
    attribution: list[tuple[str, float, str]]
    periods: list[dict]  # {key, label, weather, avg_radiation, avg_cloud}
    alert: AlertSummary | None
    daily_kwh: float
    equivalent_hours: float
    numbers: set[str] = field(default_factory=set)
    capacity_note: str | None = None  # 目录容量口径待核验时的说明

    def render(self) -> str:
        lines = [
            f"站点：{self.station_name}，{'光伏' if self.station_type == 'solar' else '风电'}，"
            f"装机 {self.capacity_kw:g} kW" + (f"，{self.address}" if self.address else ""),
            "",
            f"今日环境指数：{self.score:.0f} 分（{_level_cn(self.level)}）",
        ]
        for factor, delta, desc in self.attribution:
            lines.append(f"  {_factor_cn(factor)} {delta:+.0f} 分：{desc}")
        lines += ["", "分时段气象："]
        for p in self.periods:
            lines.append(
                f"  {p['label']} {p['range']}  {p['weather']}  "
                f"平均辐射 {p['avg_radiation']:.0f} W/m²  云量 {p['avg_cloud']:.0f}%"
            )
        lines += [
            "",
            f"预警：{self.alert.title}（{_level_cn(self.alert.level)}）"
            if self.alert
            else "预警：无",
        ]
        lines += [
            "",
            f"估算：日发电 {self.daily_kwh:,.0f} kWh，等效利用 {self.equivalent_hours:.1f} 小时",
        ]
        if self.capacity_note:
            lines.append(f"注：{self.capacity_note}")
        return "\n".join(lines)


def _level_cn(level: str) -> str:
    return {
        "excellent": "优秀",
        "good": "良好",
        "fair": "一般",
        "poor": "较差",
        "minor": "轻度",
        "moderate": "中度",
        "severe": "重度",
        "cleared": "解除",
    }.get(level, level)


def _factor_cn(f: str) -> str:
    return {"radiation": "辐射条件", "temperature": "温度损失", "wind": "散热增益"}.get(f, f)


def build_input(
    station: Station,
    fc: Forecast,
    idx: IndexResult,
    daily_kwh: float,
    alert: AlertSummary | None,
    capacity_note: str | None = None,
) -> ReportInput:
    today = fc.today()
    periods = []
    for key, label, h0, h1 in PERIODS:
        seg = today[(today.index.hour >= h0) & (today.index.hour < h1)]
        code = seg["weather_code"].mode().iloc[0] if not seg.empty else None
        periods.append(
            {
                "key": key,
                "label": label,
                "range": f"{h0:02d}:00–{h1:02d}:00",
                "weather": describe(int(code)) if code is not None and not pd.isna(code) else "—",
                "avg_radiation": float(seg["shortwave_radiation"].mean()) if not seg.empty else 0.0,
                "avg_cloud": float(seg["cloud_cover"].mean()) if not seg.empty else 0.0,
            }
        )

    inp = ReportInput(
        station_name=station.name,
        station_type=station.type,
        capacity_kw=station.capacity_kw,
        address=station.address,
        score=idx.score,
        level=idx.level.value,
        attribution=[(a.factor.value, a.delta, a.description) for a in idx.attribution],
        periods=periods,
        alert=alert,
        daily_kwh=daily_kwh,
        equivalent_hours=daily_kwh / station.capacity_kw if station.capacity_kw else 0.0,
        capacity_note=capacity_note,
    )
    inp.numbers = extract_numbers(inp.render())
    return inp


def extract_numbers(text: str) -> set[str]:
    """文本里出现过的数字（去掉千分位与正负号），用于一致性校验"""
    import re

    return {n.replace(",", "").lstrip("+-") for n in re.findall(r"[+-]?\d[\d,]*\.?\d*", text)}
