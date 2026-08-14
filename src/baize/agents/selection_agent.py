"""selection_agent — 白泽·智脑路由/元智能体模块。

Prompt: ``system_selection_agent.md``
Tools: ['check_available_agents', 'analyze_task_requirements', 'list_available_specialists', 'transfer_to_specialist']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent, AgentTool
from baize.agents.agent_discovery import AGENT_DISCOVERY_TOOLS
from baize.agents.operational_handoffs import OPERATIONAL_HANDOFF_TOOLS

AGENT_KEY = "selection_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "selection_agent"
_display_desc = "路由选择智能体 — 默认编排器，根据用户输入特征自动选择最适合的下游智能体"

# ── 工具构建 ───────────────────────────────────────────────────────
def _build_tools() -> list[AgentTool]:
    import inspect
    tools: list[AgentTool] = []

    for item in OPERATIONAL_HANDOFF_TOOLS + AGENT_DISCOVERY_TOOLS:
        name = item.get("name", "")
        description = item.get("description", "")
        handler = item.get("func", item.get("handler"))
        params = item.get("parameters", {})
        tools.append(AgentTool(
            name=name, description=description,
            parameters=params, handler=handler,
        ))

    return tools

_tools = _build_tools()

# ── Agent 实例 ─────────────────────────────────────────────────────
selection_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
