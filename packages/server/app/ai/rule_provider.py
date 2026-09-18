"""规则模板。docs/08 §六

AI 挂掉不能让页面白屏。模板的输入与 AI 完全一致（都来自 07 的指标），
所以兜底文案不会与页面数据矛盾。
"""

from app.ai.input import ReportInput
from app.ai.schema import AIReport, ReportPeriod
from app.config import settings
from app.schemas.common import ConvergenceLevel
from app.services.evolution import STABILITY_TEXT


class RuleProvider:
    name = "rule"

    async def generate_report(self, inp: ReportInput) -> AIReport:
        return render(inp)


def _verdict(score: float) -> str:
    """分档阈值统一取 settings，不另写一套 —— 否则改配置会出现「82 分 · 条件较差」。"""
    if score >= settings.index_excellent:
        return "当前气象条件优秀"
    if score >= settings.index_good:
        return "当前气象适宜发电"
    if score >= settings.index_fair:
        return "当前气象条件一般"
    return "当前气象条件较差"


def _period_level(p: dict, alert_level: str | None) -> str:
    cloud = p["avg_cloud"]
    if cloud is not None and cloud >= 60:
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
    rad = p["avg_radiation"]
    if rad is None:
        return "该时段辐射数据暂缺，请以最新预报为准"
    if rad >= 400:
        return "发电条件良好"
    if rad >= 200:
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
    elif inp.score < settings.index_fair:
        suggestions = [
            "当前气象条件较弱，请结合实测出力评估影响。",
            "设备检修与清洗应遵循现场规程，不仅依据气象评分安排。",
        ]
    # 模式自己都没拿准时，提醒临近再看比任何具体建议都更要紧，排第一。
    # 建议最多三条（AIReport 校验），有预警时本来就满了，挤掉最后一条。docs/19 §二
    if inp.stability == STABILITY_TEXT[ConvergenceLevel.SWING]:
        tip = "今天的预报在近几轮起报间来回摇摆，临近时段请以最新预报为准。"
        suggestions = [tip, *suggestions][:3]

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
