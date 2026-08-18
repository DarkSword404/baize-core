"""red_team_agent — 白泽·智脑智能体模块。

Prompt: ``system_red_team_agent.md``
Tools: ['generic_linux_command', 'execute_code', 'port_scan', 'shodan_search', 'make_web_search_with_explanation', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "red_team_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Red Team Agent"
_display_desc = "红队攻击 — 全链条攻击模拟：侦察→武器化→投递→利用→安装→C2→目标达成"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "arp_scan",
        "binwalk_analyze",
        "browser_click",
        "browser_evaluate",
        "browser_fetch",
        "browser_fill",
        "browser_screenshot",
        "crt_sh_lookup",
        "cve_lookup",
        "dns_lookup",
        "execute_code",
        "exiftool_read",
        "ffuf_fuzz",
        "generic_linux_command",
        "hashid_detect",
        "httpx_probe",
        "john_crack",
        "make_web_search_with_explanation",
        "masscan_scan",
        "netdiscover",
        "port_scan",
        "searchsploit",
        "shodan_search",
        "ssl_cert_check",
        "strings_extract",
        "think",
        "traceroute_path",
        "wafw00f_detect",
        "whatweb_identify",
        "whois_lookup",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
red_team_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
