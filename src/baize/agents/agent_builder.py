"""agent_builder — 白泽·智脑路由/元智能体模块。

Prompt: ``system_agent_builder.md``
Tools: ['list_available_tools', 'generate_agent_code', 'save_agent_file', 'generate_system_prompt']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions, extract_display_name_and_desc
from baize.sdk.agent import Agent, AgentTool
from baize.agents.agent_builder import AGENT_BUILDER_TOOLS

AGENT_KEY = "agent_builder"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name(智能跳过 Baize layering 头部注入) ──
_display_name, _display_desc = extract_display_name_and_desc(_instructions, fallback_key="agent_builder")

# ── 工具构建 ───────────────────────────────────────────────────────
def _build_tools() -> list[AgentTool]:
    import inspect
    tools: list[AgentTool] = []

    for item in AGENT_BUILDER_TOOLS:
        name = item.get("name", "")
        description = item.get("description", "")
        handler = item.get("func", item.get("handler"))
        params = item.get("parameters")
        # AgentTool.parameters 是 dataclass 必传字段，缺失时归一化为空 dict
        tools.append(AgentTool(
            name=name,
            description=description,
            parameters=params if isinstance(params, dict) else {},
            handler=handler,
        ))

    return tools

_tools = _build_tools()

# ── Agent 实例 ─────────────────────────────────────────────────────
agent_builder = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
