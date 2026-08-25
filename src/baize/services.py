"""服务注册表：全局单例服务引用，供 Agent 运行时查找。

app.py 在启动时调用 register() 注入服务，Agent 类在 _run_tool_loop 中
通过 get() 获取服务实例，避免修改每个 agent 文件的构造参数。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("baize.services")

_registry: dict[str, Any] = {}


def register(name: str, service: Any) -> None:
    """注册一个全局服务。"""
    _registry[name] = service
    logger.debug("服务已注册: %s (%s)", name, type(service).__name__)


def get(name: str) -> Optional[Any]:
    """获取已注册的服务，未注册返回 None。"""
    return _registry.get(name)


def has(name: str) -> bool:
    """检查服务是否已注册。"""
    return name in _registry