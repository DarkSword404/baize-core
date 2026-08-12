"""reasoner_supporter — 白泽智能体模块。

Prompt: ``system_reasoner_supporter.md``
Tools: ['think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "reasoner_supporter"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Reasoner Supporter"
_display_desc = "推理辅助 — 纯分析角色，提供假设推演、因果分析与不确定度评估，不直接执行攻击操作"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
reasoner_supporter = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
