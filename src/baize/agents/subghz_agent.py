"""subghz_agent — 白泽·智脑智能体模块。

Prompt: ``system_subghz_agent.md``
Tools: ['generic_linux_command', 'execute_code', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "subghz_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Sub-GHz / SDR Agent"
_display_desc = "Sub-GHz/无线电安全 — SDR 信号分析、RF 协议逆向、重放攻击及门禁系统安全评估"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "generic_linux_command",
        "execute_code",
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
subghz_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
