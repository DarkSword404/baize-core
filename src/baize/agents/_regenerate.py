"""
批量重建所有 Baize agent 模块 — v2

现在模块文件名已与 prompt 文件名对齐:
  - 模块文件: {agent_key}.py
  - Prompt: system_{agent_key}.md

工具分配按智能体能力类别定义。
"""

from __future__ import annotations

from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent

# ── 完整工具分配表 (key = prompt键名 = 模块文件名) ────────────────

AGENTS_TOOL_MAP: dict[str, list[str]] = {
    "agent_builder": ["list_available_tools", "generate_agent_code", "save_agent_file", "generate_system_prompt"],
    "android_app_logic_mapper": ["think"],
    "android_sast": ["generic_linux_command", "execute_code", "think"],
    "apt_agent": ["generic_linux_command", "execute_code", "port_scan", "shodan_search", "make_web_search_with_explanation", "think"],
    "blue_team_agent": ["generic_linux_command", "execute_code", "think"],
    "bug_bounter": ["generic_linux_command", "http_request", "execute_code", "port_scan", "shodan_search", "make_web_search_with_explanation", "think"],
    "codeagent": ["execute_code", "think"],
    "compliance_agent": ["think"],
    "continuous_ops_agent": ["generic_linux_command", "execute_code", "think"],
    "ctf_agent": ["generic_linux_command", "execute_code", "think"],
    "dfir_agent": ["generic_linux_command", "execute_code", "think"],
    "dns_smtp_agent": ["generic_linux_command", "http_request", "think"],
    "exploit_expert": ["generic_linux_command", "execute_code", "think"],
    "flag_discriminator": ["think"],
    "memory_analysis_agent": ["generic_linux_command", "execute_code", "think"],
    "network_analyzer": ["generic_linux_command", "http_request", "shodan_search", "think", "port_scan", "execute_code"],
    "orchestration_agent": ["run_specialist", "run_dual_approach_contest", "run_parallel_specialists", "check_available_agents", "analyze_task_requirements"],
    "reasoner_supporter": ["think"],
    "red_team_agent": ["generic_linux_command", "execute_code", "port_scan", "shodan_search", "make_web_search_with_explanation", "think"],
    "replay_attack_agent": ["generic_linux_command", "execute_code", "http_request", "think"],
    "reporting_agent": ["think"],
    "reverse_engineering_agent": ["generic_linux_command", "execute_code", "think"],
    "selection_agent": ["check_available_agents", "analyze_task_requirements", "list_available_specialists", "transfer_to_specialist"],
    "subghz_agent": ["generic_linux_command", "execute_code", "think"],
    "thought_router": ["think"],
    "triage_agent": ["generic_linux_command", "execute_code", "think"],
    "use_cases": ["think"],
    "web_bounty_agent": ["generic_linux_command", "http_request", "make_web_search_with_explanation", "think"],
    "web_pentester": ["generic_linux_command", "http_request", "execute_code", "port_scan", "make_web_search_with_explanation", "think"],
    "wifi_security_agent": ["generic_linux_command", "execute_code", "think"],
}

# ── 特殊声明：非标准工具来源 ─────────────────────────────────────
# 某些 agent 的工具不在 extended_tools() 中，需用特殊 import
NON_STANDARD_TOOLS: dict[str, str] = {
    # Agent Builder 使用 agent_builder 模块的工具
    "agent_builder": "AGENT_BUILDER_TOOLS",
    # Selection Agent 使用 agent_discovery + operational_handoffs 工具
    "selection_agent": "OPERATIONAL_HANDOFF_TOOLS + AGENT_DISCOVERY_TOOLS",
    # Orchestration Agent 使用 approach_contest + agent_discovery 工具
    "orchestration_agent": "APPROACH_CONTEST_TOOLS + AGENT_DISCOVERY_TOOLS",
}

NON_STANDARD_IMPORTS: dict[str, str] = {
    "agent_builder": "from baize.agents.agent_builder import AGENT_BUILDER_TOOLS",
    "selection_agent": (
        "from baize.agents.agent_discovery import AGENT_DISCOVERY_TOOLS\n"
        "from baize.agents.operational_handoffs import OPERATIONAL_HANDOFF_TOOLS"
    ),
    "orchestration_agent": (
        "from baize.agents.approach_contest import APPROACH_CONTEST_TOOLS\n"
        "from baize.agents.agent_discovery import AGENT_DISCOVERY_TOOLS"
    ),
}


def _generate_module(agent_key: str) -> str:
    """生成单个 agent 模块源码。"""
    tool_names = AGENTS_TOOL_MAP.get(agent_key, ["think"])

    if agent_key in NON_STANDARD_TOOLS:
        return _generate_special_module(agent_key, tool_names)

    tool_filter = "\n".join(f'        "{t}",' for t in tool_names)

    return f'''"""{agent_key} — 白泽智能体模块。

Prompt: ``system_{agent_key}.md``
Tools: {tool_names}
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent
from baize.tools import extended_tools

AGENT_KEY = "{agent_key}"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "{agent_key}"
_display_desc = _lines[1].lstrip("# ").strip() if len(_lines) > 1 else ""

# ── 工具筛选 ───────────────────────────────────────────────────────
_TOOL_NAMES = {{
{tool_filter}
}}
_all_tools = extended_tools()
_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]

# ── Agent 实例 ─────────────────────────────────────────────────────
{agent_key} = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
'''


def _generate_special_module(agent_key: str, tool_names: list[str]) -> str:
    """生成使用非标准工具源的特殊 agent 模块。"""
    extra_import = NON_STANDARD_IMPORTS.get(agent_key, "")
    extra_tools = NON_STANDARD_TOOLS.get(agent_key, "[]")

    return f'''"""{agent_key} — 白泽路由/元智能体模块。

Prompt: ``system_{agent_key}.md``
Tools: {tool_names}
"""

from __future__ import annotations

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent, AgentTool
{extra_import}

AGENT_KEY = "{agent_key}"

# ── 提示词 ─────────────────────────────────────────────────────────
_instructions = get_agent_instructions(AGENT_KEY)

# ── 从提示词提取 display name ────────────────────────────────────
_lines = _instructions.split("\\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "{agent_key}"
_display_desc = _lines[1].lstrip("# ").strip() if len(_lines) > 1 else ""

# ── 工具构建 ───────────────────────────────────────────────────────
def _build_tools() -> list[AgentTool]:
    import inspect
    tools: list[AgentTool] = []

    for item in {extra_tools}:
        name = item.get("name", "")
        description = item.get("description", "")
        handler = item.get("func", item.get("handler"))
        params = item.get("parameters")

        if params:
            tools.append(AgentTool(
                name=name,
                description=description,
                parameters=params,
                handler=handler,
            ))
        else:
            tools.append(AgentTool(
                name=name,
                description=description,
                handler=handler,
            ))

    return tools

_tools = _build_tools()

# ── Agent 实例 ─────────────────────────────────────────────────────
{agent_key} = Agent(
    name=_display_name,
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_tools,
)
'''


def regenerate_all():
    updated = []
    conflicted = []

    for agent_key in sorted(AGENTS_TOOL_MAP):
        filepath = AGENTS_DIR / f"{agent_key}.py"
        code = _generate_module(agent_key)
        filepath.write_text(code, encoding="utf-8")
        updated.append(agent_key)

    print(f"Regenerated {len(updated)} modules:\n")
    for key in updated:
        tools = AGENTS_TOOL_MAP[key]
        spec = "SPECIAL" if key in NON_STANDARD_TOOLS else f"{len(tools)} tools"
        print(f"  ✓ {key:30s} [{spec}] → {', '.join(tools[:3])}" + ("..." if len(tools) > 3 else ""))

    return updated


if __name__ == "__main__":
    regenerate_all()
