"""compliance_agent — 白泽·智脑智能体模块。

Prompt: ``system_compliance_agent.md``
Tools: ['think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "compliance_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "compliance_agent"
_display_desc = "GRC 风险与合规 — 对标 ISO 27001、NIST CSF、GDPR 等框架，生成差距分析与合规评估报告"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
compliance_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
