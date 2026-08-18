"""dfir_agent — 白泽·智脑智能体模块。

Prompt: ``system_dfir_agent.md``
Tools: ['generic_linux_command', 'execute_code', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "dfir_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "DFIR (Forensics & IR) Agent"
_display_desc = "数字取证与应急响应 — 硬盘镜像分析、内存取证、日志回溯与攻击时间线重建"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "binwalk_analyze",
        "cve_lookup",
        "dns_lookup",
        "execute_code",
        "exiftool_read",
        "generic_linux_command",
        "hashid_detect",
        "searchsploit",
        "strings_extract",
        "think",
        "whois_lookup",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
dfir_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
