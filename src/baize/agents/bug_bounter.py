"""bug_bounter — 白泽智能体模块。

Prompt: ``system_bug_bounter.md``
Tools: ['generic_linux_command', 'http_request', 'execute_code', 'port_scan', 'shodan_search', 'make_web_search_with_explanation', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "bug_bounter"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "bug_bounter"
_display_desc = "漏洞赏金猎人 — 自动化 Web 漏洞发现，涵盖 XSS、SQLi、SSRF、IDOR 等 OWASP Top 10 风险面"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "generic_linux_command",
        "http_request",
        "execute_code",
        "port_scan",
        "shodan_search",
        "make_web_search_with_explanation",
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
bug_bounter = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
