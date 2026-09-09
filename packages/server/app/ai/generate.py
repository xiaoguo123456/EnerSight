"""报告生成编排：调 provider → 数值一致性校验 → 失败走规则模板。docs/08 §六、§八"""

import logging
from dataclasses import dataclass

from app.ai import rule_provider
from app.ai.input import ReportInput, extract_numbers
from app.ai.provider import AIProvider
from app.ai.schema import AIReport
from app.config import settings

log = logging.getLogger(__name__)

_provider: AIProvider | None = None


def get_provider() -> AIProvider:
    global _provider
    if _provider is None:
        if settings.ai_provider == "claude":
            from app.ai.claude_provider import ClaudeProvider

            _provider = ClaudeProvider()
        else:
            _provider = rule_provider.RuleProvider()
    return _provider


def reset_provider() -> None:
    global _provider
    _provider = None


@dataclass(frozen=True)
class Generated:
    report: AIReport
    is_fallback: bool
    provider: str


def numbers_consistent(report: AIReport, allowed: set[str]) -> bool:
    """输出里的数字必须都在输入里出现过，否则判定为幻觉。docs/08 §八

    时刻（14:30）、百分比、分数都算数字。允许集合来自 ReportInput.render()。
    """
    text = " ".join(
        [
            report.verdict_title,
            report.verdict_detail,
            report.risk_title or "",
            report.risk_detail or "",
        ]
        + report.suggestions
        + [f"{p.weather_summary} {p.generation_impact}" for p in report.periods]
    )
    found = extract_numbers(text)
    # 时刻拆成小时/分钟也算命中（输入写 06:00–12:00，输出可能写 12 点）
    tokens = {_canonical(n) for n in allowed}
    for n in list(allowed):
        if ":" in n:
            tokens.update(_canonical(p) for p in n.split(":"))
    return all(_canonical(n) in tokens for n in found if n)


def _canonical(n: str) -> str:
    """12.0 与 12 视为同一个数；整数不去零 —— 否则 20、100 都会归成 2、1，
    输入里只要出现过「2 分」，编造的「下降 20%」就能混过去。"""
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n.lstrip("0") or "0"


async def generate(inp: ReportInput) -> Generated:
    provider = get_provider()
    if provider.name == "rule":
        return Generated(await provider.generate_report(inp), is_fallback=False, provider="rule")

    try:
        report = await provider.generate_report(inp)
    except Exception:  # noqa: BLE001  任何失败都降级，不让页面白屏
        log.exception("ai provider failed, falling back to rule template")
        return Generated(rule_provider.render(inp), is_fallback=True, provider="rule")

    if not numbers_consistent(report, inp.numbers):
        log.warning("ai output failed numeric consistency check, falling back")
        return Generated(rule_provider.render(inp), is_fallback=True, provider="rule")

    return Generated(report, is_fallback=False, provider=provider.name)
