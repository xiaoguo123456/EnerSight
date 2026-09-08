"""AI Provider 抽象。docs/08 §2.4

切换模型只改一个实现文件，业务代码零改动。
规则模板实现永远可用，是其他实现的降级路径。
"""

from typing import Protocol

from app.ai.input import ReportInput
from app.ai.schema import AIReport


class AIProvider(Protocol):
    name: str

    async def generate_report(self, inp: ReportInput) -> AIReport: ...
