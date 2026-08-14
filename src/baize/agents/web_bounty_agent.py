"""web_bounty_agent — 白泽·智脑智能体模块。

Prompt: ``system_web_bounty_agent.md``
Tools: ['generic_linux_command', 'http_request', 'make_web_search_with_explanation', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "web_bounty_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Web Bounty Agent"
_display_desc = "Web 漏洞赏金 (变体) — 在规则框架内自主进行 Web 安全狩猎，严格遵循赏金计划范围"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "generic_linux_command",
        "http_request",
        "make_web_search_with_explanation",
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
web_bounty_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
