"""triage_agent — 白泽·智脑智能体模块。

Prompt: ``system_triage_agent.md``
Tools: ['generic_linux_command', 'execute_code', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "triage_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "triage_agent"
_display_desc = "安全分类 — 对漏洞扫描结果、PoC 与告警进行去重、验证与优先级分级"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "generic_linux_command",
        "execute_code",
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
triage_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
