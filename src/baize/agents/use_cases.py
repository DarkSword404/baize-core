"""use_cases — 白泽·智脑智能体模块。

Prompt: ``system_use_cases.md``
Tools: ['think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "use_cases"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Use Cases Agent"
_display_desc = "安全用例生成 — 为防御、执法与授权培训场景生成案例研究与攻防演练剧本"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
use_cases = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
