"""apt_agent — 白泽·智脑智能体模块。

Prompt: ``system_apt_agent.md``
Tools: ['generic_linux_command', 'execute_code', 'port_scan', 'shodan_search', 'make_web_search_with_explanation', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "apt_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "APT (Advanced Persistent Threat) Agent"
_display_desc = "APT 高级持续性威胁模拟 — 模拟国家级对手的 MITRE ATT&CK 战术链，支持多阶段、隐蔽且可审计的攻击推演"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "generic_linux_command",
        "execute_code",
        "port_scan",
        "shodan_search",
        "make_web_search_with_explanation",
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
apt_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
