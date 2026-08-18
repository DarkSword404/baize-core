"""白泽·智脑元智能体 (Tool Builder) — 根据用户需求快速创建新的工具。

Tool Builder 是白泽·智脑的元智能体，可:
1. 分析用户需求，生成完整的工具 Python 代码
2. 在本地沙箱中试运行、校验代码
3. 自动保存为自定义工具 (~/.baize/custom/tools/*.json) 并热注册，
   保存后立即可用，无需重启服务

此模块导出两个对象:
    ToolBuilder   — 类（供外部程序化调用）
    tool_builder  — Agent 实例（供全局注册表自动发现）
"""

from __future__ import annotations

import json
from typing import Any

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent, AgentTool
from baize.tools.custom_tools import (
    CustomToolStore,
    custom_tool_store,
    derive_parameters,
    sanitize_tool_name,
    test_custom_tool,
)


# ===========================================================================
#  ToolBuilder 类 — 工具生成引擎
# ===========================================================================

class ToolBuilder:
    """根据需求生成并注册新的白泽·智脑工具。"""

    @staticmethod
    def sanitize_name(name: str) -> str:
        """将工具名称转换为合法的 snake_case 标识符。"""
        return sanitize_tool_name(name)

    @staticmethod
    def list_custom_tools() -> str:
        """列出已保存的自定义工具（含代码摘要）。"""
        records = custom_tool_store.list()
        if not records:
            return "暂无自定义工具"
        return json.dumps(
            [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "display_name": r.get("display_name", r["name"]),
                    "description": r.get("description", ""),
                    "category": r.get("category", "custom"),
                    "enabled": r.get("enabled", True),
                    "updated_at": r.get("updated_at", ""),
                }
                for r in records
            ],
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def save_tool(config: dict[str, Any], store: CustomToolStore | None = None) -> dict[str, Any]:
        """保存自定义工具（写 JSON + 热注册）。

        Args:
            config: {name, display_name?, description, category?, code, parameters?}
            store: 自定义存储（缺省用全局实例）。

        Returns:
            dict: 保存后的完整记录。

        Raises:
            ValueError: 校验失败 / 名称冲突。
        """
        store = store or custom_tool_store
        return store.create(config)

    @staticmethod
    async def test_tool(code: str, args_json: str, timeout: int = 60) -> dict[str, Any]:
        """在本地沙箱中试运行工具代码。

        Args:
            code: 工具源码。
            args_json: 测试参数 JSON 字符串。
            timeout: 执行超时（秒）。

        Returns:
            dict: {"ok": bool, "result"?: str, "error"?: str, ...}
        """
        try:
            args = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"测试参数不是合法 JSON: {exc}"}
        return await test_custom_tool(code, args, timeout=timeout)


# ===========================================================================
#  工具函数 — 供 Agent 实例使用
# ===========================================================================

def _tool_list_available_tools() -> str:
    """列出白泽·智脑平台已注册的全部工具及其描述（避免新建重名工具）。"""
    from baize.tools import registry as _tool_registry

    tools_info: dict[str, dict[str, Any]] = {}
    for spec in _tool_registry.all():
        tools_info[spec.name] = {
            "description": spec.description,
            "category": spec.category,
            "author": spec.author,
        }
    return json.dumps(tools_info, ensure_ascii=False, indent=2)


def _tool_list_custom_tools() -> str:
    """列出已保存的自定义工具（含启用状态与描述）。"""
    return ToolBuilder.list_custom_tools()


async def _tool_test_custom_tool(code: str, args_json: str = "{}", timeout: int = 60) -> str:
    """在本地沙箱中试运行工具代码并返回执行结果，用于创建/编辑时的校验。"""
    result = await ToolBuilder.test_tool(code, args_json, timeout=timeout)
    return json.dumps(result, ensure_ascii=False, indent=2)


def _tool_save_custom_tool(
    name: str,
    description: str,
    code: str,
    display_name: str = "",
    category: str = "custom",
    parameters: str | None = None,
) -> str:
    """保存自定义工具（写入 ~/.baize/custom/tools/ 并热注册，无需重启服务）。

    name: 工具名（snake_case，如 subdomain_bruteforce）。
    description: 工具描述（供 LLM 选择使用时机）。
    code: 工具源码，必须定义 def handler(...) 函数。
    display_name: 前端展示名（可选）。
    category: 分类（general/web/network/recon/crack/forensic/custom...）。
    parameters: 可选 JSON Schema 字符串；缺省自动从 handler 签名推导。
    """
    try:
        params = json.loads(parameters) if parameters else None
        if params is not None and not isinstance(params, dict):
            return "parameters 必须是 JSON 对象（{type, properties, required...}）"
        config = {
            "name": name,
            "display_name": display_name or name,
            "description": description,
            "category": category,
            "code": code,
            "parameters": params,
        }
        record = ToolBuilder.save_tool(config)
        return (
            f"工具已保存并注册，立即可用（无需重启）:\n"
            f"- 工具名: {record['name']}\n"
            f"- 展示名: {record.get('display_name', record['name'])}\n"
            f"- 分类: {record.get('category')}\n"
            f"- 描述: {record.get('description')}\n"
            f"- 参数 Schema: {json.dumps(record.get('parameters') or derive_parameters(code), ensure_ascii=False)}\n"
            f"现在即可在工具管理页面看到，并可被任意 agent 选用。"
        )
    except ValueError as exc:
        return f"保存失败: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"保存失败（未知错误）: {exc}"


def _tool_delete_custom_tool(name: str) -> str:
    """按工具名删除已保存的自定义工具（注销并移除文件）。"""
    try:
        record = custom_tool_store.find_by_name(name)
        if record is None:
            return f"未找到自定义工具 '{name}'"
        custom_tool_store.delete(record["id"])
        return f"已删除自定义工具 '{name}'"
    except Exception as exc:  # noqa: BLE001
        return f"删除失败: {exc}"


def _tool_toggle_custom_tool(name: str, enabled: bool) -> str:
    """启用/停用自定义工具（热注册/注销）。"""
    try:
        record = custom_tool_store.find_by_name(name)
        if record is None:
            return f"未找到自定义工具 '{name}'"
        updated = custom_tool_store.set_enabled(record["id"], enabled)
        state = "已启用" if updated["enabled"] else "已停用"
        return f"自定义工具 '{name}' {state}"
    except Exception as exc:  # noqa: BLE001
        return f"操作失败: {exc}"


# ===========================================================================
#  _TOOL_BUILDER_TOOLS — 工具元数据列表
# ===========================================================================

_TOOL_BUILDER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_available_tools",
        "description": "列出白泽·智脑平台已注册的全部工具及其描述，避免新建重名工具。",
        "func": _tool_list_available_tools,
    },
    {
        "name": "list_custom_tools",
        "description": "列出已保存的自定义工具（含启用状态与描述）。",
        "func": _tool_list_custom_tools,
    },
    {
        "name": "test_custom_tool",
        "description": (
            "在本地沙箱中试运行工具代码并返回执行结果，用于创建/编辑时的校验。"
            "code 为工具源码，args_json 为测试参数 JSON 字符串。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "工具源码（必须定义 handler 函数）"},
                "args_json": {"type": "string", "description": "测试参数 JSON，如 {\"target\": \"example.com\"}"},
                "timeout": {"type": "integer", "description": "执行超时秒数，默认 60"},
            },
            "required": ["code"],
        },
        "func": _tool_test_custom_tool,
    },
    {
        "name": "save_custom_tool",
        "description": (
            "将生成的工具保存为自定义工具（写入 ~/.baize/custom/tools/ 下的 JSON 并热注册），"
            "保存后立即可用，无需重启服务。code 必须定义 def handler(...) 函数。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名（snake_case）"},
                "description": {"type": "string", "description": "工具描述"},
                "code": {"type": "string", "description": "工具源码"},
                "display_name": {"type": "string", "description": "前端展示名（可选）"},
                "category": {"type": "string", "description": "分类，默认 custom"},
                "parameters": {"type": "string", "description": "可选 JSON Schema 字符串"},
            },
            "required": ["name", "description", "code"],
        },
        "func": _tool_save_custom_tool,
    },
    {
        "name": "delete_custom_tool",
        "description": "按工具名删除已保存的自定义工具（注销并移除文件）。",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "自定义工具名"}},
            "required": ["name"],
        },
        "func": _tool_delete_custom_tool,
    },
    {
        "name": "toggle_custom_tool",
        "description": "启用/停用自定义工具（热注册/注销，无需重启）。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "自定义工具名"},
                "enabled": {"type": "boolean", "description": "true 启用 / false 停用"},
            },
            "required": ["name", "enabled"],
        },
        "func": _tool_toggle_custom_tool,
    },
]


def _build_tool_builder_tools() -> list[AgentTool]:
    """将 _TOOL_BUILDER_TOOLS 列表转换为 AgentTool 列表。"""
    tools: list[AgentTool] = []
    for item in _TOOL_BUILDER_TOOLS:
        name = item.get("name", "")
        description = item.get("description", "")
        handler = item.get("func", item.get("handler"))
        params = item.get("parameters", {})
        tools.append(AgentTool(
            name=name, description=description,
            parameters=params, handler=handler,
        ))
    return tools


# ===========================================================================
#  Agent 实例 — 供 __init__.py 自动发现
# ===========================================================================

AGENT_KEY = "tool_builder"
_instructions = get_agent_instructions(AGENT_KEY)
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "Tool Builder"
_display_desc = "白泽·智脑元智能体 — 根据用户需求快速创建新的工具，支持代码生成、沙箱试运行与热注册"

tool_builder = Agent(
    name=_display_name or "Tool Builder",
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_build_tool_builder_tools(),
)
