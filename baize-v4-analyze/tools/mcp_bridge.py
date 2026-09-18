"""MCP 客户端桥接 — 把外部 MCP 服务器的工具适配进 Baize。

设计目标:
- 通过 ``mcp`` Python SDK 连接外部 MCP 服务器（stdio / SSE / HTTP transport），
  拉取其工具列表（``list_tools``），逐一把 MCP Tool 转换为标准 ``ToolSpec``，
  注册进全局 ``ToolRegistry``（或自定义注册表）。
- 工具名做**命名空间前缀**（``<server>_<tool>``）与 snake_case 卫生化，
  避免与内置工具冲突（对齐 ``custom_tools.sanitize_tool_name`` 的约定）。
- **注册前门控**: ``allow`` / ``deny`` 过滤（按原始工具名匹配）。
- **运行时门控**: 转换为 ``ToolSpec`` 后与内置工具完全同构，进入 Agent 后
  继续受 Agent 侧 ``_TOOL_NAMES`` 白名单与 ``on_tool_call`` 瀑布钩子管辖。

依赖说明:
- ``mcp`` 是**可选依赖**（``pip install "baize-core[mcp]"``）。
  本模块顶层不做任何 ``import mcp``；转换与注册层可脱离 SDK 单元测试，
  仅在真正连接服务器时惰性导入，缺失时抛出带安装指引的 ``ImportError``。

用法::

    from baize.tools.mcp_bridge import McpServerConfig, mcp_session, mcp_register_session

    config = McpServerConfig(
        name="git", transport="stdio",
        command="npx", args=["-y", "@modelcontextprotocol/server-github"],
    )

    async with mcp_session(config) as session:          # 会话期间保持连接
        specs = await mcp_register_session(session, config, allow={"get_file", "search_code"})
        # specs 已注册进全局 registry；保持 with 存活期间可用
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional, Set

from baize.tools.registry import ToolRegistry, ToolSpec, registry

logger = logging.getLogger("baize.tools.mcp_bridge")

MCP_INSTALL_HINT = '缺少可选依赖 mcp；请执行 pip install "baize-core[mcp]" 后重试'


# ===========================================================================
#  工具名卫生化（与 custom_tools.sanitize_tool_name 对齐）
# ===========================================================================

def sanitize_tool_name(name: str) -> str:
    """将名称转换为合法的工具标识符（snake_case，字母开头）。"""
    name = re.sub(r"[\s\-]+", "_", name)
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    if name and name[0].isdigit():
        name = f"tool_{name}"
    return name.lower()


def mcp_local_name(server: str, tool: str) -> str:
    """生成带服务器命名空间的本地工具名。"""
    return sanitize_tool_name(f"{server}_{tool}")


# ===========================================================================
#  配置
# ===========================================================================

@dataclass
class McpServerConfig:
    """单个 MCP 服务器连接配置。

    Attributes:
        name: 服务器标识（决定工具命名空间前缀与 author）。
        transport: stdio | sse | http。
        command: stdio 模式下启动的子进程命令（如 npx / uvx）。
        args: stdio 模式下附加参数（如 MCP 服务器包名）。
        env: stdio 模式子进程环境变量覆盖（None 表示继承父进程）。
        url: sse / http 模式下的服务器端点。
        headers: sse / http 模式附加请求头。
        timeout: 初始化超时（秒）。
    """

    name: str = "mcp"
    transport: str = "stdio"
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: Optional[dict[str, str]] = None
    url: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    timeout: float = 60.0


# ===========================================================================
#  转换层 — 与 MCP SDK 类型解耦（支持 duck-typing，便于测试）
# ===========================================================================

def _attr(obj: Any, key: str, default: Any = None) -> Any:
    """兼容对象属性与字典两种形态的字段读取。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def format_call_result(result: Any) -> str:
    """把 MCP ``call_tool`` 返回结果格式化为文本。

    处理 SDK 的 ``CallToolResult``（对象）与协议原始字典两种形态:
    - 优先取 ``structuredContent``（结构化 JSON）。
    - 否则聚合 ``content`` 中所有文本块；图像/资源等非文本块给出标记。
    - ``isError=True`` 时抛出 ``RuntimeError`` 携带错误文本。
    """
    if isinstance(result, dict):
        is_error = bool(result.get("isError", False))
        structured = result.get("structuredContent")
        content = result.get("content") or []
    else:
        is_error = bool(getattr(result, "isError", False))
        structured = getattr(result, "structuredContent", None)
        content = getattr(result, "content", None) or []

    if structured is not None:
        text = json.dumps(structured, ensure_ascii=False, default=str)
    else:
        parts: list[str] = []
        for block in content:
            btype = _attr(block, "type", "")
            if btype == "text":
                parts.append(str(_attr(block, "text", "")))
            elif btype in ("image", "resource", "resource_link"):
                parts.append(f"[{btype} content，已省略]")
            else:
                parts.append(json.dumps(block, ensure_ascii=False, default=str))
        text = "\n".join(p for p in parts if p)

    if is_error:
        raise RuntimeError(f"MCP 工具执行失败: {text or '(无错误信息)'}")
    return text or "(无输出)"


def mcp_tool_to_spec(
    server_name: str,
    mcp_tool: Any,
    *,
    handler: Callable[..., Any],
    category: str = "mcp",
) -> ToolSpec:
    """把单个 MCP Tool（SDK 对象或协议字典）转换为 ``ToolSpec``。

    Args:
        server_name: MCP 服务器名（用于命名空间与 author 标识）。
        mcp_tool: 含 ``name`` / ``description`` / ``inputSchema`` 的对象或字典。
        handler: 实际执行函数（通常由 ``make_session_handler`` 生成）。
    """
    raw_name = str(_attr(mcp_tool, "name", ""))
    if not raw_name:
        raise ValueError("MCP 工具缺少 name")
    name = mcp_local_name(server_name, raw_name)
    description = str(_attr(mcp_tool, "description", "") or "").strip() or f"MCP 工具 {raw_name}"
    input_schema = _attr(mcp_tool, "inputSchema") or {}
    return ToolSpec(
        name=name,
        description=description,
        handler=handler,
        parameters=input_schema,
        category=category,
        author=f"mcp:{sanitize_tool_name(server_name)}",
        version="1.0.0",
        tags=["mcp", sanitize_tool_name(server_name)],
    )


def make_session_handler(session: Any, tool_name: str) -> Callable[..., Any]:
    """为某个已连接 session 上的 MCP 工具生成可执行 handler。

    handler 以 **kwargs 接收 Agent 传来的参数（JSON Schema 由 inputSchema 给出），
    通过 ``session.call_tool`` 调用并格式化为文本。
    """

    async def handler(**kwargs: Any) -> str:
        result = await session.call_tool(tool_name, arguments=kwargs or None)
        return format_call_result(result)

    return handler


# ===========================================================================
#  注册层
# ===========================================================================

async def mcp_register_session(
    session: Any,
    config: McpServerConfig,
    *,
    target: Optional[ToolRegistry] = None,
    allow: Optional[Set[str]] = None,
    deny: Optional[Set[str]] = None,
    override: bool = False,
) -> list[ToolSpec]:
    """从已连接的 MCP session 拉取工具并注册为 ``ToolSpec``。

    Args:
        session: 已 initialize 的 MCP client session（支持 SDK 或协议字典形态）。
        config: 服务器配置（决定命名空间前缀与 author）。
        target: 目标注册表；None 时使用全局 ``registry``。
        allow: 只注册这些**原始工具名**（空/None 表示不限制）。
        deny: 跳过这些**原始工具名**（优先级高于 allow）。
        override: 名称冲突时是否覆盖现有注册。

    Returns:
        list[ToolSpec]: 本次实际注册的 ToolSpec（已被门控过滤）。

    Raises:
        RuntimeError: MCP 服务器返回缺少 name 的工具定义。
    """
    listing = await session.list_tools()
    tools = list(listing.get("tools") or []) if isinstance(listing, dict) else list(getattr(listing, "tools", []))
    deny_set: Set[str] = set(deny or ())
    allow_set: Set[str] = set(allow or ())
    target = target or registry

    registered: list[ToolSpec] = []
    for tool in tools:
        raw = str(_attr(tool, "name", ""))
        if not raw:
            raise RuntimeError("MCP 服务器返回了缺少 name 的工具定义")
        if raw in deny_set:
            continue
        if allow_set and raw not in allow_set:
            continue
        spec = mcp_tool_to_spec(config.name, tool, handler=make_session_handler(session, raw))
        try:
            target.register(spec, override=override)
        except ValueError as exc:
            logger.warning("跳过 MCP 工具 %s: %s", spec.name, exc)
            continue
        registered.append(spec)
        logger.info("已注册 MCP 工具 %s -> %s", config.name, spec.name)
    return registered


# ===========================================================================
#  连接层 — 惰性导入 mcp SDK
# ===========================================================================

def _load_mcp_client():
    """惰性导入 mcp SDK 客户端；缺失时抛出带安装指引的 ImportError。

    Returns:
        tuple: (ClientSession, stdio_client, sse_client)。
    """
    try:
        from mcp import ClientSession, StdioServerParameters  # noqa: F401
        from mcp.client.sse import sse_client
        from mcp.client.stdio import stdio_client
    except ImportError as exc:  # pragma: no cover - 依赖缺失分支
        raise ImportError(MCP_INSTALL_HINT) from exc
    return ClientSession, stdio_client, sse_client  # type: ignore[return-value]


async def mcp_session(config: McpServerConfig) -> AsyncIterator[Any]:
    """按配置建立到 MCP 服务器的 client session（异步上下文管理器）。

    支持 transport:
    - ``stdio``: 以子进程启动 MCP 服务器（需提供 command）。
    - ``sse`` / ``http``: 通过 URL 连接远程服务器。

    with 块存活期间 session 保持连接；退出时自动清理。

    Yields:
        Any: 已 ``initialize`` 的 client session，可直接用于
        ``mcp_register_session`` / ``session.call_tool``。
    """
    ClientSession, stdio_client, sse_client = _load_mcp_client()

    transport = (config.transport or "stdio").lower()
    if transport in ("stdio", "command"):
        if not config.command:
            raise ValueError("stdio transport 需要提供 command")
        from mcp import StdioServerParameters

        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env,
        )
        streams = stdio_client(params)
    elif transport in ("sse", "http", "https"):
        if not config.url:
            raise ValueError(f"{transport} transport 需要提供 url")
        streams = sse_client(config.url, headers=config.headers or None, timeout=config.timeout)
    else:
        raise ValueError(f"不支持的 MCP transport: {config.transport}（支持 stdio/sse/http）")

    async with streams as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


__all__ = [
    "McpServerConfig",
    "format_call_result",
    "make_session_handler",
    "mcp_local_name",
    "mcp_register_session",
    "mcp_session",
    "mcp_tool_to_spec",
    "sanitize_tool_name",
]
