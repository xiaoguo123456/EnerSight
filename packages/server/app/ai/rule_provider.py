"""规则模板。docs/08 §六

AI 挂掉不能让页面白屏。模板的输入与 AI 完全一致（都来自 07 的指标），
所以兜底文案不会与页面数据矛盾。
"""

from app.ai.input import ReportInput
from app.ai.schema import AIReport, ReportPeriod


class RuleProvider:
    name = "rule"

    async def generate_report(self, inp: ReportInput) -> AIReport:
        return render(inp)


def _verdict(score: float) -> str:
    if score >= 85:
        return "今日发电条件优秀"
    if score >= 70:
        return "今日适宜发电"
    if score >= 55:
        return "今日发电条件一般"
    return "今日发电条件较差"


def _period_level(p: dict, alert_level: str | None) -> str:
    if p["key"] == "afternoon" and alert_level in ("moderate", "severe"):
        return "risk"
    if p["avg_cloud"] >= 60:
        return "warning"
    return "good"


def _impact(p: dict, station_type: str) -> str:
    if p["key"] == "evening":
        return "对次日无明显影响" if p["avg_cloud"] < 70 else "云量偏多，关注次日早间"
    if station_type == "wind":
        return "按计划运行"
    if p["avg_radiation"] >= 400:
        return "发电条件良好"
    if p["avg_radiation"] >= 200:
        return "发电效率可能下降"
    return "辐射偏弱"


def render(inp: ReportInput) -> AIReport:
    alert_level = inp.alert.level if inp.alert else None
    base = _verdict(inp.score)
    if inp.alert and alert_level in ("moderate", "severe"):
        title = f"{base}，下午存在{'中度' if alert_level == 'moderate' else '重度'}云层风险"
    elif inp.alert:
        title = f"{base}，存在轻度云层风险"
    else:
        title = base

    periods = [
        ReportPeriod(
            period=p["key"],
            weather_summary=p["weather"],
            generation_impact=_impact(p, inp.station_type),
            level=_period_level(p, alert_level),
        )
        for p in inp.periods
    ]

    suggestions = ["按计划满负荷运行，保持常规巡检。", "关注明日天气变化，提前安排运行计划。"]
    if inp.alert:
        when = inp.alert.title.split("后")[0] if "后" in inp.alert.title else "下午"
        suggestions = [
            f"重点关注{when}后的天气变化，适时调整发电计划。",
            "加强下午时段的实时监测，若云量持续增加，可启用功率平滑控制减少输出波动。",
            "关注未来 24 小时天气变化，提前做好设备巡检。",
        ]
    elif inp.score < 55:
        suggestions = [
            "今日发电条件不佳，可安排设备检修与清洗。",
            "关注明日天气转好时段，提前恢复满负荷运行。",
        ]

    return AIReport(
        verdict_title=title,
        verdict_detail=(
            f"环境指数 {inp.score:.0f} 分。"
            + ("建议按计划运行，关注下午云量变化。" if inp.alert else "建议按计划运行。")
        ),
        periods=periods,
        risk_title=inp.alert.title if inp.alert else None,
        risk_detail=inp.alert.description if inp.alert else None,
        suggestions=suggestions,
    )
