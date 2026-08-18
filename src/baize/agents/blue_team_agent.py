"""blue_team_agent — 白泽·智脑智能体模块。

Prompt: ``system_blue_team_agent.md``
Tools: ['generic_linux_command', 'execute_code', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "blue_team_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Blue Team Agent"
_display_desc = "蓝队防御 — 基于遥测与配置证据驱动检测、溯源与加固，覆盖 SIEM、EDR、IDS 及防火墙日志分析"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "cve_lookup",
        "dns_lookup",
        "execute_code",
        "generic_linux_command",
        "hashid_detect",
        "searchsploit",
        "think",
        "traceroute_path",
        "whois_lookup",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
blue_team_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
