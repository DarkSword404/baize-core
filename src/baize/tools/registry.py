"""Baize 标准工具协议与动态注册表。

提供:
- ``ToolSpec``: 标准工具定义（名称/描述/参数 Schema/执行器/元数据）。
- ``ToolRegistry``: 全局工具注册表，支持注册、注销、查询与 entry point 发现。
- ``register_tool``: 声明式装饰器，函数即工具，自动从类型注解推导 JSON Schema。
- ``discover_entry_points``: 加载 ``baize.tools`` entry point 组中的外部工具插件。

设计目标:
- 与 ``AgentTool`` 完全兼容（``ToolSpec.to_agent_tool()`` 可转换为 AgentTool）。
- 第三方插件通过 `pip install` 安装后，注册 ``baize.tools`` entry point，
  启动时自动发现并注册，无需修改核心源码 —— 对齐 LangChain 的集成包模式。
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Callable, Optional, Union, get_type_hints

from baize.sdk.agent import AgentTool

logger = logging.getLogger("baize.tools.registry")


# ===========================================================================
#  JSON Schema 推导（从函数类型注解 / 默认值）
# ===========================================================================

def _type_to_schema(annotation: Any) -> dict[str, Any]:
    """将 Python 类型注解映射为 JSON Schema 片段（支持常见类型）。"""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())
    if origin in (list, set, tuple):
        item = _type_to_schema(args[0]) if args else {}
        return {"type": "array", "items": item}
    if origin is dict:
        return {"type": "object"}
    if origin is Union:
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        if len(non_none) == 1:
            return _type_to_schema(non_none[0])
        return {"anyOf": [_type_to_schema(a) for a in non_none]}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    # 其他类型（Enum、Literal、自定义类等）按字符串处理
    return {"type": "string"}


def schema_from_signature(fn: Callable) -> dict[str, Any]:
    """从函数签名推导 OpenAI 风格 JSON Schema（parameters）。

    支持 str/int/float/bool/list/dict/Optional 等常见类型，
    带默认值的参数视为可选；未注解的参数按 string 处理。
    """
    try:
        hints = get_type_hints(fn)
    except Exception:  # noqa: BLE001
        hints = {}

    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = hints.get(name, param.annotation)
        prop = _type_to_schema(annotation)
        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(name)
        properties[name] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


# ===========================================================================
#  ToolSpec — 标准工具协议
# ===========================================================================

@dataclass
class ToolSpec:
    """标准工具协议。

    Attributes:
        name: 工具唯一名称（函数调用时的标识符）。
        description: 工具功能描述（供 LLM 选择）。
        handler: 执行函数（同步或异步）。
        parameters: 显式 JSON Schema；为 None 时从 handler 签名自动推导。
        category: 工具分类（general/web/network/forensic/...）。
        author: 作者/来源标识（内置为 "baize"，插件可自定义）。
        version: 工具版本号。
        tags: 附加标签。
    """

    name: str
    description: str
    handler: Callable[..., Any]
    parameters: Optional[dict[str, Any]] = None
    category: str = "general"
    author: str = "baize"
    version: str = "1.0.0"
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.parameters is None:
            self.parameters = schema_from_signature(self.handler)

    def to_schema(self) -> dict[str, Any]:
        """转 OpenAI function calling schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {},
            },
        }

    def to_agent_tool(self) -> AgentTool:
        """转换为运行时使用的 ``AgentTool``（与现有执行框架兼容）。"""
        return AgentTool(
            name=self.name,
            description=self.description,
            parameters=self.parameters or {},
            handler=self.handler,
        )


# ===========================================================================
#  ToolRegistry — 全局注册表
# ===========================================================================

class ToolRegistry:
    """线程安全的全局工具注册表。

    支持按名称注册/注销/查询，按分类枚举，以及通过 entry point
    发现第三方工具插件（``baize.tools`` 组）。
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._loaded_plugins: set[str] = set()

    # ---- 注册 / 注销 -------------------------------------------------
    def register(self, spec: ToolSpec, *, override: bool = False) -> None:
        """注册一个工具。

        Args:
            spec: 工具定义。
            override: 名称已存在时是否覆盖（默认 False，冲突抛 ValueError）。

        Raises:
            ValueError: 工具名重复且 override=False。
        """
        if not spec.name:
            raise ValueError("工具名称不能为空")
        if spec.name in self._tools and not override:
            raise ValueError(
                f"工具 '{spec.name}' 已注册（来源: {self._tools[spec.name].author}），"
                "如确需覆盖请使用 override=True"
            )
        self._tools[spec.name] = spec

    def unregister(self, name: str) -> None:
        """注销一个工具。"""
        self._tools.pop(name, None)

    # ---- 查询 ---------------------------------------------------------
    def get(self, name: str) -> Optional[ToolSpec]:
        """按名称获取工具定义。"""
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        """返回全部工具（按注册顺序）。"""
        return list(self._tools.values())

    def names(self) -> list[str]:
        """返回全部工具名。"""
        return list(self._tools.keys())

    def by_category(self, category: str) -> list[ToolSpec]:
        """按分类返回工具。"""
        return [t for t in self._tools.values() if t.category == category]

    def categories(self) -> list[str]:
        """返回全部分类。"""
        return sorted({t.category for t in self._tools.values()})

    # ---- 兼容层 -------------------------------------------------------
    def to_agent_tools(self) -> list[AgentTool]:
        """将全部已注册工具转换为 ``AgentTool`` 列表（兼容旧 API）。"""
        return [spec.to_agent_tool() for spec in self._tools.values()]

    # ---- entry point 插件发现 -----------------------------------------
    def discover_entry_points(self, group: str = "baize.tools") -> int:
        """扫描指定 entry point 组并加载外部工具插件。

        每个 entry point 可以是:
        1. 可调用对象 ``fn(registry: ToolRegistry) -> None``（推荐）。
        2. 模块（加载后查找 ``register`` 或 ``register_tools`` 函数调用之）。
        3. 模块级属性 ``tools``: 可迭代的 ToolSpec / AgentTool。

        Returns:
            int: 本次新发现的插件数量。
        """
        try:
            eps = entry_points(group=group)
        except TypeError:
            # Python 3.10/3.11 兼容
            eps = entry_points().get(group, [])

        discovered = 0
        for ep in eps:
            if ep.name in self._loaded_plugins:
                continue
            try:
                loaded = ep.load()
                if callable(loaded):
                    loaded(self)
                elif hasattr(loaded, "register"):
                    loaded.register(self)
                elif hasattr(loaded, "register_tools"):
                    loaded.register_tools(self)
                elif hasattr(loaded, "tools"):
                    for tool in loaded.tools:
                        self._register_legacy(tool)
                else:
                    logger.warning("插件 %s 未导出 register/register_tools/tools，已跳过", ep.name)
                    continue
                self._loaded_plugins.add(ep.name)
                discovered += 1
                logger.info("已加载工具插件: %s (名称: %s)", ep.value, ep.name)
            except Exception as exc:  # noqa: BLE001
                logger.error("加载工具插件 %s 失败: %s", ep.name, exc, exc_info=True)
        return discovered

    def _register_legacy(self, tool: Any) -> None:
        """将 AgentTool 或 ToolSpec 注册进注册表。"""
        if isinstance(tool, ToolSpec):
            self.register(tool)
        elif isinstance(tool, AgentTool):
            self.register(ToolSpec(
                name=tool.name,
                description=tool.description,
                handler=tool.handler,
                parameters=tool.parameters,
                author="plugin",
            ))


# 全局唯一注册表实例
registry = ToolRegistry()


# ===========================================================================
#  register_tool — 声明式注册装饰器
# ===========================================================================

def register_tool(
    name: Optional[str] = None,
    *,
    description: Optional[str] = None,
    category: str = "general",
    author: str = "baize",
    version: str = "1.0.0",
    parameters: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
    override: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """声明式工具注册装饰器。

    用法::

        @register_tool(description="执行端口扫描", category="network")
        def port_scan(host: str, ports: str = "1-1000") -> str:
            ...

    工具名默认取函数名；参数 Schema 从函数签名自动推导，
    也可通过 ``parameters`` 显式提供。

    Returns:
        原函数（不改变其行为）。
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or fn.__name__
        spec = ToolSpec(
            name=tool_name,
            description=description or fn.__doc__ or tool_name,
            handler=fn,
            parameters=parameters,
            category=category,
            author=author,
            version=version,
            tags=tags or [],
        )
        registry.register(spec, override=override)
        return fn

    return decorator
