"""orchestration_agent — 白泽·智脑路由/元智能体模块。

Prompt: ``system_orchestration_agent.md``
Tools: ['run_specialist', 'run_dual_approach_contest', 'run_parallel_specialists', 'check_available_agents', 'analyze_task_requirements', 'shared_browser_open', 'shared_browser_wait_user', 'shared_browser_snapshot', 'shared_browser_click', 'shared_browser_fill', 'shared_browser_evaluate', 'shared_browser_status', 'shared_browser_close']
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions, extract_display_name_and_desc
from baize.sdk.agent import Agent, AgentTool
from baize.agents.approach_contest import APPROACH_CONTEST_TOOLS
from baize.agents.agent_discovery import AGENT_DISCOVERY_TOOLS

AGENT_KEY = "orchestration_agent"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name(智能跳过 Baize layering 头部注入) ──
_display_name, _display_desc = extract_display_name_and_desc(_instructions, fallback_key="orchestration_agent")

# ── 工具构建 ───────────────────────────────────────────────────────
def _build_tools() -> list[AgentTool]:
    import inspect
    tools: list[AgentTool] = []

    for item in APPROACH_CONTEST_TOOLS + AGENT_DISCOVERY_TOOLS:
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

    # 共享协作浏览器工具（处理验证码/扫码登录/人机验证等 AI 无法自动完成的登录步骤）
    from baize.tools import extended_tools as _sb_extended_tools
    _sb_all = _sb_extended_tools()
    _sb_keep = ["shared_browser_open", "shared_browser_wait_user", "shared_browser_snapshot", "shared_browser_click", "shared_browser_fill", "shared_browser_evaluate", "shared_browser_status", "shared_browser_close"]
    for _sb_t in _sb_all:
        if _sb_t.name in _sb_keep:
            tools.append(_sb_t)

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
