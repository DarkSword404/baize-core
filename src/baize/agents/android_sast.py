"""android_sast — 白泽·智脑智能体模块。

Prompt: ``system_android_sast.md``
Tools: ['generic_linux_command', 'execute_code', 'think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "android_sast"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Android SAST Agent"
_display_desc = "Android 静态安全分析 — 对 APK 执行白盒审计，检测 WebView、IPC、权限滥用及硬编码凭据等常见风险"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "execute_code",
        "exiftool_read",
        "generic_linux_command",
        "hashid_detect",
        "searchsploit",
        "strings_extract",
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
android_sast = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
