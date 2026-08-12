"""orchestration_agent — 白泽路由/元智能体模块。

Prompt: ``system_orchestration_agent.md``
Tools: ['run_specialist', 'run_dual_approach_contest', 'run_parallel_specialists', 'check_available_agents', 'analyze_task_requirements']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent, AgentTool
from baize.agents.approach_contest import APPROACH_CONTEST_TOOLS
from baize.agents.agent_discovery import AGENT_DISCOVERY_TOOLS

AGENT_KEY = "orchestration_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "orchestration_agent"
_display_desc = "编排智能体 — 默认入口智能体，负责解析用户意图并协调下游子智能体进行任务分发与结果汇总"

# ── 工具构建 ───────────────────────────────────────────────────────
def _build_tools() -> list[AgentTool]:
    import inspect
    tools: list[AgentTool] = []

    for item in APPROACH_CONTEST_TOOLS + AGENT_DISCOVERY_TOOLS:
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
orchestration_agent = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
