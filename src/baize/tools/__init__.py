"""Baize 工具集合（独立实现）。

提供智能体可调用的安全分析工具，并按类别划分为不同工具集，
使不同智能体拥有差异化的工具配置（对齐 Baize v1.0.0 的智能体工具差异）。

工具统一注册到标准注册表（``baize.tools.registry``），支持:
- 内置工具自动注册
- 第三方插件通过 ``baize.tools`` entry point 动态发现（无需修改源码）
"""

from __future__ import annotations

from baize.sdk.agent import AgentTool
from baize.tools.registry import ToolSpec, register_tool, registry

# ----------------------------------------------------------------------
# 注册内置工具（来自 extended.py 的既有实现，保持行为一致）
# ----------------------------------------------------------------------
from baize.tools.extended import extended_tools as _builtin_extended_tools

for _t in _builtin_extended_tools():
    if registry.get(_t.name) is None:
        registry.register(
            ToolSpec(
                name=_t.name,
                description=_t.description,
                handler=_t.handler,
                parameters=_t.parameters,
                category="general",
                author="baize",
            )
        )

# 注册封装的安全工具（nmap/sqlmap/tshark 等，通过 register_tool 装饰器）
from baize.tools import security_tools as _security_tools  # noqa: F401

# 注册扩展安全工具（信息收集/漏洞/爆破/无线/取证，通过 register_tool 装饰器）
from baize.tools import security_tools_extra as _security_tools_extra  # noqa: F401

# 注册浏览器自动化工具（页面侦察/链接提取/表单分析/截图）
from baize.tools import browser_tools as _browser_tools  # noqa: F401

# 发现已安装的工具插件（baize.tools entry point）
registry.discover_entry_points()

_tool_index = {t.name: t for t in registry.to_agent_tools()}


def extended_tools() -> list[AgentTool]:
    """返回扩展工具的完整集合（内置 + 插件注册的全部工具）。"""
    return registry.to_agent_tools()


def command_tool() -> AgentTool:
    """返回通用命令执行工具。"""
    return _tool_index["generic_linux_command"]


def http_tool() -> AgentTool:
    """返回 HTTP 请求工具。"""
    return _tool_index["http_request"]


def port_scan_tool() -> AgentTool:
    """返回端口扫描工具。"""
    return _tool_index["port_scan"]


# ----------------------------------------------------------------------
# 按智能体类别划分的工具集（对齐 Baize v1.0.0 的差异化工具配置）
# ----------------------------------------------------------------------

def _pick(*names: str) -> list[AgentTool]:
    """按名称从工具索引中挑选工具。"""
    return [_tool_index[n] for n in names if n in _tool_index]


# 核心工具集（Web 渗透 / 侦察 / CTF）
def web_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "http_request", "port_scan",
        "execute_code", "make_web_search_with_explanation",
    )


# 红队工具集（含 SSH、搜索）
def redteam_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "http_request", "port_scan",
        "run_ssh_command_with_credentials", "make_web_search_with_explanation",
        "execute_code",
    )


# 蓝队工具集（防御：SSH、Web、shell、分析）
def blueteam_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "http_request",
        "run_ssh_command_with_credentials", "execute_code",
        "analyze_task_requirements", "check_available_agents",
    )


# DFIR 取证工具集（shell、搜索、代码、分析）
def dfir_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "execute_code",
        "make_web_search_with_explanation", "shodan_search",
        "analyze_task_requirements", "think",
    )


# 逆向工程工具集
def reverse_engineering_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "shodan_search", "execute_code", "think",
    )


# 合规工具集
def compliance_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "verify_csv_inventory",
        "analyze_task_requirements", "think",
    )


# 报告工具集（无工具——仅整合对话）
def reporting_tools() -> list[AgentTool]:
    return []


# 复测工具集（Web + HTTP + shell）
def retester_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "http_request", "execute_code",
    )


# 邮件安全工具集
def mail_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "make_web_search_with_explanation",
    )


# 编码智能体工具集
def codeagent_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "execute_code",
    )


# 连续运维工具集
def continuous_ops_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "analyze_task_requirements",
        "check_available_agents", "think",
    )


# Sub-GHz / SDR 工具集
def subghz_sdr_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "execute_code", "think",
    )


# 编排工具集
def orchestration_tools() -> list[AgentTool]:
    return _pick(
        "analyze_task_requirements", "check_available_agents", "think",
    )


# 选择工具集
def selection_tools() -> list[AgentTool]:
    return _pick(
        "check_available_agents", "think",
    )


# 通用助手工具集
def general_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "http_request", "make_web_search_with_explanation",
    )


# 网络分析工具集
def network_analysis_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "execute_code",
    )


# 无线安全工具集
def wifi_security_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "execute_code", "think",
    )


# Android SAST 工具集
def android_sast_tools() -> list[AgentTool]:
    return _pick(
        "generic_linux_command", "execute_code",
    )


# 安全工具集（任务 3：封装的外部安全工具，通过注册表提供）
def security_tools() -> list[AgentTool]:
    """返回封装的外部安全工具（nmap/sqlmap/tshark 等，若已注册）。"""
    return [t.to_agent_tool() for t in registry.all() if t.category == "security"]


__all__ = [
    "registry",
    "register_tool",
    "ToolSpec",
    "extended_tools",
    "command_tool",
    "http_tool",
    "port_scan_tool",
    "web_tools",
    "redteam_tools",
    "blueteam_tools",
    "dfir_tools",
    "reverse_engineering_tools",
    "compliance_tools",
    "reporting_tools",
    "retester_tools",
    "mail_tools",
    "codeagent_tools",
    "continuous_ops_tools",
    "subghz_sdr_tools",
    "orchestration_tools",
    "selection_tools",
    "general_tools",
    "network_analysis_tools",
    "wifi_security_tools",
    "android_sast_tools",
    "security_tools",
]
