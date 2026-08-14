"""codeagent — 白泽·智脑智能体模块。

Prompt: ``system_codeagent.md``
Tools: ['execute_code', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "codeagent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "codeagent"
_display_desc = "CodeAgent (CodeAct) — 面向代码理解、审查与重构的通用编程智能体，支持多语言与复杂工程上下文"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "execute_code",
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
codeagent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
