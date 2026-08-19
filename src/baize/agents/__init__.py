"""
白泽·智脑 (Baize) 智能体注册表
========================

从 ``baize.agents`` 模块自动发现并注册所有 Agent 实例。

每个 agent 模块只需导出 Agent 实例变量和相应的变体列表（若存在），
``__init__.py`` 会自动扫描并注册。

约定：
    - 每个模块可以导出 ``_VARIANTS`` 列表（可选），列出需要注册的
      变体 Agent 实例引用（每个变体必须有 ``name`` 和 ``instructions``
      属性）。
    - 如果模块没有 ``_VARIANTS``，则只会注册模块主 Agent 实例。
    - 主 Agent 实例的变量名必须与 :data:`_MODULE_AGENT_VARS` 中
      列出的变量名之一匹配。
"""

from __future__ import annotations

import importlib
import os
import traceback
from typing import Any, Dict, List, Optional
from pathlib import Path

from baize.sdk.agent import Agent
from baize.prompts_util import get_agent_instructions

# ---------------------------------------------------------------------------
# 全局注册表
# ---------------------------------------------------------------------------
_AGENTS: Dict[str, Agent] = {}

# 别名映射：别名 → 注册名（如模块 AGENT_KEY "web_pentester" → "Web Application Pentester"）
_AGENT_ALIASES: Dict[str, str] = {}

# ---------------------------------------------------------------------------
# 定义每个模块中主 Agent 实例的变量名（按模块文件名）
#
# 约定: 模块文件名 = prompt 键名（如 system_red_team_agent.md → red_team_agent.py）
#       变量名与模块文件名相同（如 red_team_agent = Agent(...)）
# ---------------------------------------------------------------------------
_MODULE_AGENT_VARS: Dict[str, str] = {
    "agent_builder": "agent_builder",
    "android_app_logic_mapper": "android_app_logic_mapper",
    "android_sast": "android_sast",
    "apt_agent": "apt_agent",
    "blue_team_agent": "blue_team_agent",
    "bug_bounter": "bug_bounter",
    "codeagent": "codeagent",
    "compliance_agent": "compliance_agent",
    "continuous_ops_agent": "continuous_ops_agent",
    "ctf_agent": "ctf_agent",
    "dfir_agent": "dfir_agent",
    "dns_smtp_agent": "dns_smtp_agent",
    "exploit_expert": "exploit_expert",
    "flag_discriminator": "flag_discriminator",
    "memory_analysis_agent": "memory_analysis_agent",
    "network_analyzer": "network_analyzer",
    "orchestration_agent": "orchestration_agent",
    "reasoner_supporter": "reasoner_supporter",
    "red_team_agent": "red_team_agent",
    "replay_attack_agent": "replay_attack_agent",
    "reporting_agent": "reporting_agent",
    "reverse_engineering_agent": "reverse_engineering_agent",
    "selection_agent": "selection_agent",
    "subghz_agent": "subghz_agent",
    "thought_router": "thought_router",
    "tool_builder": "tool_builder",
    "triage_agent": "triage_agent",
    "use_cases": "use_cases",
    "web_bounty_agent": "web_bounty_agent",
    "web_pentester": "web_pentester",
    "wifi_security_agent": "wifi_security_agent",
}

# ---------------------------------------------------------------------------
# 自动发现
# ---------------------------------------------------------------------------

def _discover_and_register() -> None:
    """扫描当前目录下所有 agent 模块，导入并注册 Agent 实例。"""
    agents_dir = Path(__file__).resolve().parent

    # 基础设施模块 — 它们只提供函数/类，不包含 Agent 实例
    _INFRA_MODULES: set[str] = {
        "agent_discovery",
        "approach_contest",
        "guardrails",
        "operational_handoffs",
    }

    for py_file in sorted(agents_dir.glob("*.py")):
        mod_name = py_file.stem

        # 跳过内部/基础设施模块
        if mod_name.startswith("_"):
            continue
        if mod_name == "__init__":
            continue
        if mod_name in _INFRA_MODULES:
            continue
        # 跳过不在注册表映射中的模块（遗留文件或不包含 Agent）
        if mod_name not in _MODULE_AGENT_VARS:
            continue

        try:
            module = importlib.import_module(f"baize.agents.{mod_name}")
        except Exception:
            print(f"[baize.agents] 无法导入模块 {mod_name}:")
            traceback.print_exc()
            continue

        # 获取主 Agent 变量名
        agent_var_name = _MODULE_AGENT_VARS.get(mod_name)

        # 检查是否有 _VARIANTS 列表
        variants = getattr(module, "_VARIANTS", None)
        if isinstance(variants, (list, tuple)):
            # 注册所有变体
            for agent in variants:
                if isinstance(agent, Agent) and hasattr(agent, "name"):
                    register_agent(agent.name, agent)
                else:
                    print(f"[baize.agents] 跳过 {mod_name}._VARIANTS 中非 Agent 实例: {agent!r}")
            # 变体模式：仍将模块名注册为别名（指向首个变体）
            if variants and isinstance(variants[0], Agent):
                register_agent_alias(mod_name, variants[0].name)
            continue  # 变体模式：不注册主 Agent

        # 标准模式：注册主 Agent 实例
        if agent_var_name:
            agent = getattr(module, agent_var_name, None)
            if isinstance(agent, Agent) and hasattr(agent, "name"):
                register_agent(agent.name, agent)
                # 模块名（AGENT_KEY）注册为别名，便于编排模板引用
                key = getattr(module, "AGENT_KEY", mod_name)
                register_agent_alias(key, agent.name)
                register_agent_alias(mod_name, agent.name)
            else:
                print(
                    f"[baize.agents] {mod_name}.{agent_var_name} 不是有效的 Agent 实例 (type={type(agent).__name__})"
                )
        else:
            print(f"[baize.agents] {mod_name} 未在 _MODULE_AGENT_VARS 中注册")


# ---------------------------------------------------------------------------
# Agent 注册 / 删除
# ---------------------------------------------------------------------------

def register_agent(name: str, agent: Agent) -> None:
    """将 Agent 注册到全局注册表。

    Args:
        name: Agent 唯一名称（推荐使用 agent.name）。
        agent: Agent 实例。
    """
    _AGENTS[name] = agent


def register_agent_alias(alias: str, agent_name: str) -> None:
    """为 Agent 注册别名（如模块名 AGENT_KEY、snake_case 名等）。

    便于编排模板等以非 display name 引用 Agent，对齐
    ``get_agent`` 的别名解析能力。

    Args:
        alias: 别名（如 "web_pentester"、"recon_agent"）。
        agent_name: 已注册的 Agent 名称。
    """
    if not alias:
        return
    _AGENT_ALIASES[alias] = agent_name


def unregister_agent(name: str) -> None:
    """从注册表中移除指定 Agent。

    Args:
        name: Agent 名称。
    """
    _AGENTS.pop(name, None)
    # 同时清理指向该 Agent 的别名
    for alias, target in list(_AGENT_ALIASES.items()):
        if target == name:
            del _AGENT_ALIASES[alias]





# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def list_agents() -> List[Dict[str, Any]]:
    """列出已注册的全部智能体。

    Returns:
        List[dict]: 每个元素包含 name, description, instructions, type, source 字段。
    """
    # 内置集群型智能体 → pattern_type 标记，供前端 Swarm 集群选择使用
    _PATTERN_TYPE_MAP = {
        "Selection Agent (default orchestrator)": "swarm",
        "Orchestration Agent (default entry)": "swarm",
    }
    result = []
    for name, agent in _AGENTS.items():
        result.append({
            "name": agent.name,
            "id": agent.name,
            "description": agent.description,
            "instructions": agent.instructions,
            "type": "agent",
            "source": "builtin",
            "tools": [
                {"name": t.name, "description": t.description or ""}
                for t in agent.tools
            ] if agent.tools else [],
            "pattern_type": _PATTERN_TYPE_MAP.get(agent.name),
        })
    return result


def get_agent(name: Optional[str] = None) -> Optional[Agent]:
    """按名称获取 Agent 对象。

    解析顺序:
    1. 精确名称匹配（Agent.name / display name）。
    2. 别名匹配（模块 AGENT_KEY、snake_case 别名等）。
    3. 大小写不敏感匹配（名称与别名）。
    4. ``None`` 时返回第一个注册的 Agent。

    Args:
        name: Agent 名称或别名。如果为 None，返回第一个注册的 Agent。

    Returns:
        Agent | None: 找到的 Agent 实例。
    """
    if name is None:
        if _AGENTS:
            return next(iter(_AGENTS.values()))
        return None

    # 1. 精确匹配
    if name in _AGENTS:
        return _AGENTS[name]

    # 2. 别名匹配
    if name in _AGENT_ALIASES:
        target = _AGENT_ALIASES[name]
        return _AGENTS.get(target)

    # 3. 大小写不敏感匹配（名称与别名）
    name_lower = name.lower()
    for reg_name, agent in _AGENTS.items():
        if reg_name.lower() == name_lower:
            return agent
    for alias, target in _AGENT_ALIASES.items():
        if alias.lower() == name_lower:
            return _AGENTS.get(target)

    return None


def aliases_for(agent_name: str) -> List[str]:
    """返回某智能体的全部注册别名（如模块 AGENT_KEY、snake_case 名）。

    与 ``register_agent_alias`` 对应，供编排目录 / 报错信息向 LLM
    暴露可用名称，避免 LLM 只能依赖 display name。

    Args:
        agent_name: 智能体注册名（display name）。

    Returns:
        List[str]: 按注册顺序的别名列表；无别名时返回空列表。
    """
    return [alias for alias, target in _AGENT_ALIASES.items() if target == agent_name]


def list_tools() -> List[Dict[str, str]]:
    """列出当前平台可用的全部工具。

    Returns:
        List[dict]: 每个元素包含 name 和 description。
    """
    from baize.tools import extended_tools

    tools = []
    for t in extended_tools():
        tools.append({
            "name": t.name,
            "description": t.description or "",
        })
    return tools


# ---------------------------------------------------------------------------
# 启动时执行
# ---------------------------------------------------------------------------
_discover_and_register()
