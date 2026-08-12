"""android_app_logic_mapper — 白泽智能体模块。

Prompt: ``system_android_app_logic_mapper.md``
Tools: ['think']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "android_app_logic_mapper"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_display_name = "Android App Logic Mapper"
_display_desc = "Android 应用逻辑映射 — 对反编译后的 APK 代码进行组件识别、数据流映射与安全架构分析"

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {
        "think",
}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
android_app_logic_mapper = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
