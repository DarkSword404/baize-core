"""network_analyzer — 白泽智能体模块。

Prompt: ``system_network_analyzer.md``
Tools: ['generic_linux_command', 'http_request', 'shodan_search', 'think', 'port_scan', 'execute_code']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "network_analyzer"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Network Analyzer"
_display_desc = "网络流量分析 — PCAP 深度解析、协议还原、C2 通信检测与异常流量识别"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "generic_linux_command",
        "http_request",
        "shodan_search",
        "think",
        "port_scan",
        "execute_code",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
network_analyzer = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
