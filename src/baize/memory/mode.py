"""长期记忆的访问模式。

记忆是参考材料，不应默认强制注入 agent 上下文。模式：

- ``off``  ：不自动召回，也不提供 memory_search 工具
- ``tool`` ：不自动注入内容；agent 通过 memory_search 按需查询（默认）
- ``brief``：保留少量自动召回摘要，明确标注为“可能相关、可忽略”
- ``auto`` ：与 brief 相同（兼容旧配置）
"""
from __future__ import annotations

import os

_VALID_MODES = frozenset({"off", "tool", "brief", "auto"})


def memory_mode() -> str:
    raw = (os.getenv("BAIZE_MEMORY_MODE") or "tool").strip().lower()
    return raw if raw in _VALID_MODES else "tool"


def memory_auto_inject_enabled() -> bool:
    return memory_mode() in ("brief", "auto")


def memory_tool_enabled() -> bool:
    return memory_mode() != "off"
