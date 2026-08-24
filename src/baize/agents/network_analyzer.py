"""network_analyzer — 白泽·智脑智能体模块。

Prompt: ``system_network_analyzer.md``
Tools: ['generic_linux_command', 'http_request', 'shodan_search', 'think', 'port_scan', 'execute_code', 'shared_browser_open', 'shared_browser_wait_user', 'shared_browser_snapshot', 'shared_browser_click', 'shared_browser_fill', 'shared_browser_evaluate', 'shared_browser_status', 'shared_browser_close']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions, extract_display_name_and_desc
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "network_analyzer"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name(智能跳过 Baize layering 头部注入) ──
_display_name, _display_desc = extract_display_name_and_desc(_instructions, fallback_key="network_analyzer")

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "generic_linux_command",
        "http_request",
        "shodan_search",
        "think",
        "port_scan",
        "execute_code",
        "shared_browser_open",
        "shared_browser_wait_user",
        "shared_browser_snapshot",
        "shared_browser_click",
        "shared_browser_fill",
        "shared_browser_evaluate",
        "shared_browser_status",
        "shared_browser_close",
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
