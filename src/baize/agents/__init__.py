"""
白泽·智脑 (Baize) 智能体注册表
========================

内置智能体已废弃——对话走黑板驱动的动态 agent 自动编排
（见 ``baize.pentest.conversation_orchestrator``），
流水线模板中的 agent 节点在找不到已注册 agent 时回退到
``DynamicAgentFactory`` 临时创建。

本模块仅保留注册表基础设施（``get_agent`` / ``list_agents`` /
``register_agent`` 等），供：
- 编排 ``orchestration/nodes/agent.py`` 按名称查找 agent
- ``app.py`` 的 guardrails / tools 接口
- 自定义 agent（CustomAgentStore）注册
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from baize.sdk.agent import Agent

# ---------------------------------------------------------------------------
# 全局注册表
# ---------------------------------------------------------------------------
_AGENTS: Dict[str, Agent] = {}
_AGENT_ALIASES: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Agent 注册 / 删除
# ---------------------------------------------------------------------------

def register_agent(name: str, agent: Agent) -> None:
    """将 Agent 注册到全局注册表。"""
    _AGENTS[name] = agent


def register_agent_alias(alias: str, agent_name: str) -> None:
    """为 Agent 注册别名（便于编排模板等以非 display name 引用）。"""
    if alias:
        _AGENT_ALIASES[alias] = agent_name


def unregister_agent(name: str) -> None:
    """从注册表中移除指定 Agent。"""
    _AGENTS.pop(name, None)
    for alias, target in list(_AGENT_ALIASES.items()):
        if target == name:
            del _AGENT_ALIASES[alias]


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def list_agents() -> List[Dict[str, Any]]:
    """列出已注册的全部智能体（内置已废弃，仅返回自定义注册的）。"""
    result = []
    for name, agent in _AGENTS.items():
        result.append({
            "name": agent.name,
            "id": agent.name,
            "description": agent.description,
            "instructions": agent.instructions,
            "type": "agent",
            "source": "custom",
            "tools": [
                {"name": t.name, "description": t.description or ""}
                for t in agent.tools
            ] if agent.tools else [],
        })
    return result


def get_agent(name: Optional[str] = None) -> Optional[Agent]:
    """按名称获取 Agent 对象。

    解析顺序:
    1. 精确名称匹配
    2. 别名匹配
    3. 大小写不敏感匹配
    4. ``None`` 时返回第一个注册的 Agent（可能为空）
    """
    if name is None:
        if _AGENTS:
            return next(iter(_AGENTS.values()))
        return None

    if name in _AGENTS:
        return _AGENTS[name]
    if name in _AGENT_ALIASES:
        return _AGENTS.get(_AGENT_ALIASES[name])

    name_lower = name.lower()
    for reg_name, agent in _AGENTS.items():
        if reg_name.lower() == name_lower:
            return agent
    for alias, target in _AGENT_ALIASES.items():
        if alias.lower() == name_lower:
            return _AGENTS.get(target)

    return None


def aliases_for(agent_name: str) -> List[str]:
    """返回某智能体的全部注册别名。"""
    return [alias for alias, target in _AGENT_ALIASES.items() if target == agent_name]


def list_tools() -> List[Dict[str, str]]:
    """列出当前平台可用的全部工具。"""
    from baize.tools import extended_tools

    tools = []
    for t in extended_tools():
        tools.append({
            "name": t.name,
            "description": t.description or "",
        })
    return tools
