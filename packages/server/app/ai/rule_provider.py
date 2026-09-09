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
        return "当前气象条件优秀"
    if score >= 70:
        return "当前气象适宜发电"
    if score >= 55:
        return "当前气象条件一般"
    return "当前气象条件较差"


def _period_level(p: dict, alert_level: str | None) -> str:
    if p["avg_cloud"] >= 60:
        return "warning"
    return "good"


def _impact(p: dict, station_type: str) -> str:
    if p["key"] == "evening":
        return (
            "夜间不评估光伏出力，次日请查看新预报"
            if station_type == "solar"
            else "夜间仍需结合风速与设备条件评估"
        )
    if station_type == "wind":
        return "结合风速趋势与设备条件评估出力"
    if p["avg_radiation"] >= 400:
        return "发电条件良好"
    if p["avg_radiation"] >= 200:
        return "辐射资源中等，非设备效率评价"
    return "辐射偏弱"


def render(inp: ReportInput) -> AIReport:
    alert_level = inp.alert.level if inp.alert else None
    base = _verdict(inp.score)
    if inp.alert and alert_level in ("moderate", "severe"):
        title = f"{base}，需关注{'中度' if alert_level == 'moderate' else '重度'}气象风险"
    elif inp.alert:
        title = f"{base}，存在轻度气象风险"
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

    suggestions = [
        "结合设备状态、电网调度与气象条件评估运行安排。",
        "查看后续气象趋势，使用实测出力核对模型估算。",
    ]
    if inp.alert:
        suggestions = [
            "核对预警发布时间与作用时段，关注相关气象变化。",
            "结合现场监测与调度要求评估影响，不直接依据本报告调整设备。",
            "查看最新预报，及时复核当前风险是否仍然有效。",
        ]
    elif inp.score < 55:
        suggestions = [
            "当前气象条件较弱，请结合实测出力评估影响。",
            "设备检修与清洗应遵循现场规程，不仅依据气象评分安排。",
        ]

    return AIReport(
        verdict_title=title,
        verdict_detail=(
            f"发电适宜度 {inp.score:.0f} 分。"
            + (
                "请结合风险时段与现场数据判断。"
                if inp.alert
                else "此评分仅反映气象条件，不代表设备健康或实际出力。"
            )
        ),
        periods=periods,
        risk_title=inp.alert.title if inp.alert else None,
        risk_detail=inp.alert.description if inp.alert else None,
        suggestions=suggestions,
    )
