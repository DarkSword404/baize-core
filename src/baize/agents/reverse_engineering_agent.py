"""reverse_engineering_agent — 白泽智能体模块。

Prompt: ``system_reverse_engineering_agent.md``
Tools: ['generic_linux_command', 'execute_code', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "reverse_engineering_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Reverse Engineering Agent"
_display_desc = "逆向工程 — 二进制/固件/协议逆向，使用 Ghidra、IDA、Radare2 等工具进行静态与动态分析"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "generic_linux_command",
        "execute_code",
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
reverse_engineering_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
