"""dns_smtp_agent — 白泽·智脑智能体模块。

Prompt: ``system_dns_smtp_agent.md``
Tools: ['generic_linux_command', 'http_request', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "dns_smtp_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "dns_smtp_agent"
_display_desc = "DNS/SMTP 邮件安全 — SPF、DKIM、DMARC 配置审计，邮件头分析与邮件欺诈检测"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "crt_sh_lookup",
        "cve_lookup",
        "dns_lookup",
        "generic_linux_command",
        "http_request",
        "think",
        "traceroute_path",
        "whois_lookup",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
dns_smtp_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
