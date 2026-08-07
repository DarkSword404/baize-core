"""Baize 智能体注册表。

智能体基于 Baize 独立实现的 :class:`~baize.sdk.agent.Agent` 框架定义。
智能体指令以中文重新编写，保留各智能体的核心能力与工作流程。
"""

from __future__ import annotations

from typing import Any

from baize.sdk.agent import Agent, AgentTool

from .core_ctf import ctf_agent
from .web_pentest import web_pentester_agent
from .network_analysis import network_analysis_agent
from .recon import recon_agent
from .redteam import redteam_agent
from .general import general_agent
from .blue_teamer import blueteam_agent
from .dfir import dfir_agent
from .compliance_agent import compliance_agent
from .reverse_engineering_agent import reverse_engineering_agent
from .wifi_security_tester import wifi_security_agent
from .reporter import reporting_agent
from .retester import retester_agent
from .mail import dns_smtp_agent
from .android_sast_agent import android_sast_agent
from .codeagent import codeagent
from .continuous_ops_agent import continuous_ops_agent
from .subghz_sdr_agent import subghz_sdr_agent
from .orchestration_agent import orchestration_agent
from .selection_agent import selection_agent

# 默认智能体（通用助手）
DEFAULT_AGENT_NAME = "general_agent"

_AGENTS: dict[str, Agent] = {}


def _register() -> None:
    for a in (
        general_agent,
        ctf_agent,
        web_pentester_agent,
        network_analysis_agent,
        recon_agent,
        redteam_agent,
        blueteam_agent,
        dfir_agent,
        compliance_agent,
        reverse_engineering_agent,
        wifi_security_agent,
        reporting_agent,
        retester_agent,
        dns_smtp_agent,
        android_sast_agent,
        codeagent,
        continuous_ops_agent,
        subghz_sdr_agent,
        orchestration_agent,
        selection_agent,
    ):
        _AGENTS[a.name] = a


_register()


def get_agent(name: str | None) -> Agent | None:
    if not name:
        name = DEFAULT_AGENT_NAME
    return _AGENTS.get(name)


def list_agents() -> list[dict[str, Any]]:
    """返回智能体元数据（供前端展示）。tools 为对象数组 [{name, description}]。"""
    out = []
    for name, agent in _AGENTS.items():
        out.append(
            {
                "name": name,
                "id": name,
                "description": getattr(agent, "description", ""),
                "tools": [{"name": t.name, "description": t.description} for t in agent.tools],
            }
        )
    return out


def list_tools() -> list[dict[str, Any]]:
    """返回所有可用工具的元数据。"""
    seen: dict[str, AgentTool] = {}
    for agent in _AGENTS.values():
        for t in agent.tools:
            seen.setdefault(t.name, t)
    return [
        {
            "name": t.name,
            "description": t.description,
        }
        for t in seen.values()
    ]
