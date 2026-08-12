"""flag_discriminator — 白泽智能体模块。

Prompt: ``system_flag_discriminator.md``
Tools: ['think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "flag_discriminator"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "flag_discriminator"
_display_desc = "Flag 鉴别器 — 从大量文本中快速识别与提取 CTF flag 格式字符串，辅助自动化竞赛管道"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
flag_discriminator = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
