"""场站级出力约束：限电第一层。docs/17 §四、docs/07 §2.6

规则只作用于电量与功率，不作用于环境指数。物理出力算完之后按配置时段封顶：

    ratio     全天出力 × (1 − ratio_percent / 100)
    schedule  时段内出力封顶为 装机 × limit_percent / 100，时段外不变

时段按墙钟小时的起点匹配。光伏 v4 是区间末标签，需要减去输入步长后匹配
（15 分钟输入的 13:00 对应 12:45–13:00，归属 12 点）；风电瞬时样本直接匹配。调用方通过 interval_end 说明标签口径。
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Window:
    start_hour: int
    end_hour: int  # 不含；start >= end 表示跨零点
    limit_percent: float
    weekdays: tuple[int, ...]  # ISO 1–7，空表示每天

    def covers(self, ts: pd.Timestamp) -> bool:
        # 跨午夜的后半段归属开始日，例如周五 22–02 包含周六凌晨。
        owner = (
            ts - pd.Timedelta(days=1)
            if self.start_hour > self.end_hour and ts.hour < self.end_hour
            else ts
        )
        if self.weekdays and owner.isoweekday() not in self.weekdays:
            return False
        h = ts.hour
        if self.start_hour < self.end_hour:
            return self.start_hour <= h < self.end_hour
        return h >= self.start_hour or h < self.end_hour


@dataclass(frozen=True)
class Rule:
    mode: str  # ratio | schedule
    ratio_percent: float | None
    windows: tuple[Window, ...]


def parse(raw: dict | None) -> Rule | None:
    """站点 JSON → Rule。无规则、mode 为 none 或内容为空时返回 None，调用方按无约束处理。"""
    if not raw or not isinstance(raw, dict):
        return None
    mode = raw.get("mode")
    if mode == "ratio":
        ratio = raw.get("ratio_percent")
        if not isinstance(ratio, int | float) or not 0 < ratio <= 100:
            return None
        return Rule("ratio", float(ratio), ())
    if mode == "schedule":
        windows = []
        for w in raw.get("windows") or []:
            try:
                windows.append(
                    Window(
                        int(w["start_hour"]),
                        int(w["end_hour"]),
                        float(w["limit_percent"]),
                        tuple(int(d) for d in (w.get("weekdays") or [])),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return Rule("schedule", None, tuple(windows)) if windows else None
    return None


def apply(
    hourly_kw: pd.Series,
    rule: Rule | None,
    capacity_kw: float,
    *,
    interval_end: bool,
    step_minutes: int = 60,
) -> pd.Series:
    """可发出力 → 计入约束后的上网出力。NaN 原样保留，不把缺测当 0。

    interval_end 为 True 时标签是区间末，按区间起点（减一个 step）匹配墙钟小时。
    """
    if rule is None:
        return hourly_kw
    values = hourly_kw.to_numpy(dtype=float).copy()
    if rule.mode == "ratio":
        assert rule.ratio_percent is not None
        return pd.Series(values * (1.0 - rule.ratio_percent / 100.0), index=hourly_kw.index)
    back = pd.Timedelta(minutes=step_minutes)
    for i, label in enumerate(hourly_kw.index):
        ts = pd.Timestamp(label) - back if interval_end else pd.Timestamp(label)
        caps = [w.limit_percent for w in rule.windows if w.covers(ts)]
        if caps and np.isfinite(values[i]):
            values[i] = min(values[i], capacity_kw * min(caps) / 100.0)
    return pd.Series(values, index=hourly_kw.index)


def describe(rule: Rule | None) -> str | None:
    """一句话说明，进预测假设与 AI 输入。"""
    if rule is None:
        return None
    if rule.mode == "ratio":
        return f"按用户填写的固定限电比例 {rule.ratio_percent:g}% 折减"
    parts = []
    for w in rule.windows:
        names = "".join("一二三四五六日"[d - 1] for d in w.weekdays)
        days = "每天" if not w.weekdays else f"周{names}"
        parts.append(f"{days} {w.start_hour:02d}–{w.end_hour:02d} 时出力上限 {w.limit_percent:g}%")
    return "按用户填写的分时段出力上限封顶：" + "；".join(parts)
