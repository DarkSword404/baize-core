"""Baize 工具集合（独立实现）。

提供智能体可调用的安全分析工具。工具统一注册到标准注册表
（``baize.tools.registry``），支持:

- 内置工具自动注册
- 第三方插件通过 ``baize.tools`` entry point 动态发现（无需修改源码）

各智能体的差异化工具配置由 agent 模块自身的 ``_TOOL_NAMES`` 白名单
从 ``extended_tools()`` 全集中筛选实现（见 ``baize.agents.web_pentester``
等），不再在此处维护按类别的工具集函数。
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

# 注册共享协作浏览器工具（有头持久化、人机共用、扫码登录）
from baize.tools import shared_browser as _shared_browser  # noqa: F401

# 发现已安装的工具插件（baize.tools entry point）
registry.discover_entry_points()

# MCP 客户端桥接（可选依赖 mcp；惰性导入，不影响未安装场景）
from baize.tools import mcp_bridge  # noqa: E402,F401


def extended_tools() -> list[AgentTool]:
    """返回扩展工具的完整集合（内置 + 插件注册的全部工具）。"""
    return registry.to_agent_tools()


__all__ = [
    "registry",
    "register_tool",
    "ToolSpec",
    "extended_tools",
]
