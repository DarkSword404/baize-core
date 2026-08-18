"""continuous_ops_agent — 白泽·智脑智能体模块。

Prompt: ``system_continuous_ops_agent.md``
Tools: ['generic_linux_command', 'execute_code', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "continuous_ops_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Continuous Ops Agent"
_display_desc = "持续安全运营 — 周期性扫描、漏洞验证、监控告警，支撑常态化安全运维自动化"

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
continuous_ops_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
