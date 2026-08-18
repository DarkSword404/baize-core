"""thought_router — 白泽·智脑智能体模块。

Prompt: ``system_thought_router.md``
Tools: ['think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "thought_router"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Thought Router"
_display_desc = "思路路由器 — 将复杂任务分解为有序的执行阶段，规划作战路线与所需证据"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "dns_lookup",
        "think",
        "whois_lookup",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
thought_router = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
