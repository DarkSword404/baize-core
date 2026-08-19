"""
白泽·智脑操作路由系统 — 供 Selection Agent 智能体间调度使用

提供 ``build_operational_handoffs`` 函数，为每个专项智能体生成专用路由，
通过 ``/agent <number>`` 命令实现智能体间的控制转移。

与编排智能体 (Orchestration Agent) 的区别:
- **Selection Agent**: 使用路由 (handoff) — 将控制权转交给专项智能体
- **Orchestration Agent**: 使用工具 (contest) — 专项智能体作为 worker，编排者保持会话控制
"""

from __future__ import annotations

import importlib
from typing import Any


def _specialist_specs() -> list[tuple[str, str, str, str]]:
    """返回 (模块路径, 变量名, 显示名, 路由描述) 四元组列表。"""
    return [
        (
            "baize.agents.red_team_agent",
            "red_team_agent",
            "Red Team Agent",
            "广义进攻: 渗透测试、漏洞利用、提权、shell/CLI 侦察和攻击链。"
            "不适用于纯 Web 穿透。",
        ),
        (
            "baize.agents.blue_team_agent",
            "blue_team_agent",
            "Blue Team Agent",
            "防御工作: 检测工程、IR 预案、安全加固、SOC 级分诊、"
            "日志/规则调优及蓝队演练。",
        ),
        (
            "baize.agents.bug_bounter",
            "bug_bounter",
            "Bug Bounter Agent",
            "漏洞赏金风格排查: 限定范围的 web/API/移动应用测试、"
            "PoC 及负责任披露。正式 web 渗透评估应用 Web Pentester。",
        ),
        (
            "baize.agents.dfir_agent",
            "dfir_agent",
            "DFIR Agent",
            "DFIR: 磁盘/内存取证证据、时间线、恶意软件分级、"
            "证据保管链及 breach 后调查。",
        ),
        (
            "baize.agents.reverse_engineering_agent",
            "reverse_engineering_agent",
            "Reverse Engineering Agent",
            "静/动态逆向: 二进制、固件、恶意软件家族、脱壳及底层行为分析。"
            "不适用于通用编码任务。",
        ),
        (
            "baize.agents.network_analyzer",
            "network_analyzer",
            "Network Analyzer Agent",
            "网络数据核心: PCAP/pcapng、流记录、协议分析、"
            "包级深度检测和流量基线。",
        ),
        (
            "baize.agents.wifi_security_agent",
            "wifi_security_agent",
            "WiFi Security Agent",
            "无线专项: Wi-Fi 评估、RF/无线协议、无线电层安全。"
            "不适用于通用 IP 渗透。",
        ),
        (
            "baize.agents.memory_analysis_agent",
            "memory_analysis_agent",
            "Memory Analysis Agent",
            "内存取证: 内存转储、进程内存、运行时证据"
            "(如 Volatility 风格分析)。",
        ),
        (
            "baize.agents.reporting_agent",
            "reporting_agent",
            "Reporting Agent",
            "专业交付: 正式报告、执行摘要、结构化白皮书、"
            "面向利益方文档。",
        ),
        (
            "baize.agents.ctf_agent",
            "ctf_agent",
            "CTF Agent",
            "CTF 风格挑战、单步 shell 命令、轻量工具。"
            "不适用于持续软件开发。",
        ),
        (
            "baize.agents.web_bounty_agent",
            "web_bounty_agent",
            "Replay Attack Agent",
            "验证/复现: 误报消除、复现检测、修复后回归验证。",
        ),
        (
            "baize.agents.web_pentester",
            "web_pentester",
            "Web Pentester Agent",
            "聚焦 Web 应用/API 渗透测试和结构化安全评估。"
            "区别于 bounty 风格的随机发现。",
        ),
        (
            "baize.agents.apt_agent",
            "apt_agent",
            "APT Agent",
            "APT 对手模拟: 定向渗透场景、攻防演练、高级持续性威胁叙事。"
            "需授权范围。",
        ),
        (
            "baize.agents.use_cases",
            "use_cases",
            "Use Cases Agent",
            "结构化安全演练场景模板和攻防推演。"
            "适用于需要步骤化策略引导的场景。",
        ),
        (
            "baize.agents.compliance_agent",
            "compliance_agent",
            "Compliance Agent",
            "GRC 与合规映射: NIS2, CRA, ISO 27001, IEC 62443, 控制措施, "
            "证据包及差距分析。",
        ),
        (
            "baize.agents.codeagent",
            "codeagent",
            "Code Agent",
            "重度编码: 多文件项目、重构、测试脚手架、"
            "迭代实现。不适用于一次性的 shell 片段。",
        ),
        (
            "baize.agents.continuous_ops_agent",
            "continuous_ops_agent",
            "Continuous Ops Agent",
            "周期/长期监控与分诊循环，支持免终端后台执行和"
            "API 速率感知调度。",
        ),
    ]


def _build_handoff_agent_info(agent_key: str, display_name: str, route_desc: str) -> dict[str, Any]:
    """为单个智能体构建路由信息字典。"""
    from baize.agents import aliases_for, get_agent

    # 路由表使用 snake_case 键名，须经 get_agent 解析到注册的显示名
    agent = get_agent(agent_key) or get_agent(display_name)
    if agent is None:
        return {
            "key": agent_key,
            "name": display_name,
            "aliases": aliases_for(display_name),
            "available": False,
            "description": route_desc,
        }

    return {
        "key": agent_key,
        "name": agent.name,
        "aliases": aliases_for(agent.name),
        "available": True,
        "description": route_desc,
        "agent_description": agent.description or "",
        "tools_count": len(agent.tools or []),
    }


def build_operational_handoffs() -> list[dict[str, Any]]:
    """构建全部操作智能体的路由信息列表。

    供 Selection Agent 在运行时分派时使用。
    每条记录包含智能体的键名、显示名、描述和可用性。

    Returns:
        list[dict]: 智能体路由信息列表，每项含 key/name/description/available。
    """
    out: list[dict[str, Any]] = []
    for _mod_path, _attr, display_name, route_desc in _specialist_specs():
        # 从模块路径提取 agent key (baize.agents.xxx)
        agent_key = _mod_path.split(".")[-1]
        info = _build_handoff_agent_info(agent_key, display_name, route_desc)
        out.append(info)
    return out


# ---------------------------------------------------------------------------
# Selection Agent 路由工具
# ---------------------------------------------------------------------------

def _get_handoff_list() -> str:
    """列出所有可用路由智能体（JSON 格式）。"""
    import json
    handoffs = build_operational_handoffs()
    available = [h for h in handoffs if h.get("available")]
    return json.dumps({
        "total": len(handoffs),
        "available": len(available),
        "agents": available,
    }, ensure_ascii=False, indent=2)


def _transfer_to_agent(agent_key: str) -> str:
    """将控制权转交给指定智能体。

    Args:
        agent_key: 智能体名称 — 显示名或注册键名/别名
            (如 ``Web Application Pentester`` 或 ``web_pentester``)。
    """
    import json
    from baize.agents import _AGENTS, aliases_for, get_agent

    agent = get_agent((agent_key or "").strip())
    if agent is None:
        available = ", ".join(
            f"{name} ({', '.join(aliases_for(name))})" if aliases_for(name) else name
            for name in sorted(_AGENTS.keys())
        )
        return json.dumps({
            "status": "error",
            "message": f"未找到智能体 '{agent_key}'",
            "available_agents": available,
        }, ensure_ascii=False)

    return json.dumps({
        "status": "transferred",
        "agent_key": agent_key,
        "agent_name": agent.name,
        "agent_aliases": aliases_for(agent.name),
        "agent_description": agent.description or "",
        "instructions": "控制权已转交给目标智能体。请在新上下文中继续完成用户任务。",
    }, ensure_ascii=False)


OPERATIONAL_HANDOFF_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_available_specialists",
        "description": (
            "列出所有可用专项智能体及路由描述。"
            "在以下场景使用: 用户询问有哪些智能体、需要选择最佳专项智能体处理任务。"
        ),
        "func": _get_handoff_list,
    },
    {
        "name": "transfer_to_specialist",
        "description": (
            "将控制权转交给指定专项智能体。"
            "使用前提: 已确认目标智能体适合用户任务，且无需保持 selection 层面的会话控制。"
            "如果需要在同一会话中调度多个智能体并综合结果，应使用编排智能体。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_key": {
                    "type": "string",
                    "description": "目标智能体名称 — 显示名或注册键名/别名"
                    "(如 'Web Application Pentester' 或 'web_pentester')",
                },
            },
            "required": ["agent_key"],
        },
        "func": _transfer_to_agent,
    },
]
