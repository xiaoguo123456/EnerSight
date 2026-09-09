"""Claude 实现。docs/08 §2.3、§4.2、§5.3

- 结构化输出：messages.parse + Pydantic，不让模型自由发挥后正则解析
- 固定前缀（system）打缓存断点，站点数据放 messages
- 境内公开发布应换成已备案模型：实现同一个 Protocol 即可
"""

import asyncio

import anthropic

from app.ai.input import ReportInput
from app.ai.schema import AIReport
from app.config import settings

SYSTEM_PROMPT = """你是新能源电站的运维分析助手，为光伏与风电站点生成每日分析。

规则：
1. 只解释给定数据，不做任何算术，不引入未提供的数字
2. 面向电站运维人员，用运营语言而非气象术语
3. 每条运营建议必须是可执行动作，不写「注意天气」这类空话
4. 无风险时如实说明，risk_title 与 risk_detail 置为 null，不要为了填满结构编造风险
5. 中文输出，简洁，不使用 emoji 与 markdown 标记
6. 未提供设备实测和调度数据，不得给出满负荷运行、启停、功率控制或检修指令。
   仅给出核对数据和评估气象影响的建议。
   不能仅依据晚间云量推断次日影响，不能把辐射资源降低称为设备转换效率下降
7. periods 固定三段：morning / afternoon / evening，按输入顺序
"""


class ClaudeProvider:
    name = "claude"

    def __init__(self) -> None:
        # 密钥从 ANTHROPIC_API_KEY 环境变量读取，不进配置文件
        self._client = anthropic.Anthropic(timeout=settings.ai_timeout_seconds)

    async def generate_report(self, inp: ReportInput) -> AIReport:
        # SDK 是同步的，丢进线程池
        return await asyncio.get_running_loop().run_in_executor(None, self._call, inp)

    def _call(self, inp: ReportInput) -> AIReport:
        response = self._client.messages.parse(
            model=settings.ai_model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # 固定前缀打断点。前缀里不能有时间戳等每次变化的内容
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": inp.render()}],
            output_format=AIReport,
        )
        report = response.parsed_output
        if report is None:
            raise RuntimeError("structured output parse failed")
        return report
