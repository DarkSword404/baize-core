"""白泽元智能体 (Agent Builder) — 根据用户需求自动生成新的安全智能体。

Agent Builder 是白泽的元智能体，可:
1. 分析用户需求，生成完整的智能体配置
2. 自动保存为自定义智能体 (~/.baize/custom/agents/*.json)
3. 保存后立即可用，无需重启，也不涉及任何动态代码加载

此模块导出两个对象:
    AgentBuilder  — 类（供外部程序化调用）
    agent_builder  — Agent 实例（供全局注册表自动发现）
"""

from __future__ import annotations

import json
import os
import re
import secrets
import textwrap
from datetime import datetime, timezone
from typing import Any

from baize.prompts_util import get_agent_instructions
from baize.sdk.agent import Agent, AgentTool


# ===========================================================================
#  AgentBuilder 类 — 代码生成引擎
# ===========================================================================

class AgentBuilder:
    """根据配置生成完整的白泽智能体 Python 模块文件。"""

    # 白泽可用工具 → 导入语句映射
    TOOL_IMPORTS: dict[str, str] = {
        "generic_linux_command": "from baize.tools.extended import _run_shell as generic_linux_command",
        "execute_code": "from baize.tools.extended import _execute_code as execute_code",
        "http_request": "from baize.tools.extended import _http_request as http_request",
        "run_ssh_command_with_credentials": "from baize.tools.extended import _ssh_command as run_ssh_command_with_credentials",
        "make_web_search_with_explanation": "from baize.tools.extended import _web_search as make_web_search_with_explanation",
        "shodan_search": "from baize.tools.extended import _shodan_search as shodan_search",
        "port_scan": "from baize.tools.extended import _port_scan as port_scan",
        "analyze_task_requirements": "from baize.tools.extended import _analyze_task_requirements as analyze_task_requirements",
        "check_available_agents": "from baize.tools.extended import _check_available_agents as check_available_agents",
        "think": "from baize.tools.extended import _think as think",
    }

    # 工具名 → 描述信息
    AVAILABLE_TOOLS_INFO: dict[str, dict[str, Any]] = {
        "generic_linux_command": {
            "name": "generic_linux_command",
            "description": "在本地沙箱中执行 Linux/Unix shell 命令并返回输出。",
        },
        "execute_code": {
            "name": "execute_code",
            "description": "执行 Python 代码片段并返回输出。",
        },
        "http_request": {
            "name": "http_request",
            "description": "发起 HTTP/HTTPS 请求并返回响应（含 SSRF 防护）。",
        },
        "run_ssh_command_with_credentials": {
            "name": "run_ssh_command_with_credentials",
            "description": "通过 SSH 在远程主机执行命令。",
        },
        "make_web_search_with_explanation": {
            "name": "make_web_search_with_explanation",
            "description": "执行 Web 搜索获取最新信息。",
        },
        "shodan_search": {
            "name": "shodan_search",
            "description": "使用 Shodan 搜索互联网暴露设备与服务 (需要 SHODAN_API_KEY)。",
        },
        "port_scan": {
            "name": "port_scan",
            "description": "对目标执行 TCP 端口扫描。",
        },
        "think": {
            "name": "think",
            "description": "记录并输出中间推理过程（不执行操作）。",
        },
    }

    @staticmethod
    def sanitize_name(name: str) -> str:
        """将智能体名称转换为合法的 Python 标识符。"""
        name = re.sub(r"[\s\-]+", "_", name)
        name = re.sub(r"[^a-zA-Z0-9_]", "", name)
        if name and name[0].isdigit():
            name = f"agent_{name}"
        return name.lower()

    @staticmethod
    def _escape_embedded(s: str) -> str:
        """转义嵌入生成的 Python 模块字符串中的内容。

        LLM 生成的提示词/描述可能含字面引号、反斜杠或三引号序列
        （如 flag{...} 示例、代码片段、文档分隔线），直接嵌入会生成
        语法错误的模块，这里统一转义以保证生成的代码始终可编译。
        """
        return s.replace("\\", "\\\\").replace('"', '\\"')

    @classmethod
    def build_agent_file(cls, config: dict[str, Any]) -> str:
        """根据配置生成完整的智能体 Python 模块代码。

        Args:
            config: {name, description, system_prompt, tools: [str]}

        Returns:
            str: 完整的 Python 模块源代码。
        """
        agent_name = cls.sanitize_name(config["name"])
        display_name = config["name"]
        description = config.get("description", f"白泽自动生成的 {display_name} 智能体")
        system_prompt = config["system_prompt"]
        tools = config.get("tools", [])

        # 构建导入部分
        lines = [
            f'"""{cls._escape_embedded(display_name)} Agent',
            "",
            f"由白泽智能体工厂 (Agent Builder) 自动生成。",
            f"",
            f"{cls._escape_embedded(description)}",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from baize.prompts_util import get_agent_instructions",
            "from baize.sdk.agent import Agent",
            "from baize.tools import extended_tools",
            "",
        ]

        # 构建系统提示词
        lines.append("# ── 系统提示词 ───────────────────────────────────────")
        # 单行嵌入三引号字符串，避免字面量保留首尾换行导致内容与输入不一致
        lines.append(f'{agent_name}_system_prompt = """{cls._escape_embedded(system_prompt.strip())}"""')

        # 构建工具配置
        tool_names_str = ", ".join(f'"{cls._escape_embedded(t)}"' for t in tools) if tools else ""
        lines.append("")
        lines.append("# ── 工具筛选 ─────────────────────────────────────────")
        if tool_names_str:
            lines.append(f"_TOOL_NAMES = {{{tool_names_str}}}")
        else:
            lines.append("_TOOL_NAMES = set()")
        lines.append("_all_tools = extended_tools()")
        lines.append(f"{agent_name}_tools = [t for t in _all_tools if t.name in _TOOL_NAMES]")

        # 构建 Agent 实例
        lines.append("")
        lines.append("# ── Agent 实例 ───────────────────────────────────────")
        lines.append(f"{agent_name} = Agent(")
        lines.append(f'    name="{cls._escape_embedded(display_name)}",')
        lines.append(f'    description="""{cls._escape_embedded(description)}""",')
        lines.append(f"    instructions={agent_name}_system_prompt,")
        lines.append("    model=None,")
        lines.append(f"    tools={agent_name}_tools,")
        lines.append(")")

        return "\n".join(lines) + "\n"

    @classmethod
    def save_agent_file(cls, config: dict[str, Any], base_path: str | None = None) -> str:
        """将生成的智能体保存为自定义智能体 JSON（~/.baize/custom/agents/）。

        生成的智能体以结构化数据保存，由 CustomAgentStore 加载后立即可用，
        无需重启服务，也不涉及任何动态代码执行。同名智能体已存在时复用其
        id 进行更新（upsert），避免重复堆积。

        Args:
            config: 智能体配置（name/description/system_prompt/tools 等）。
            base_path: 已废弃参数，保留仅为兼容历史调用。

        Returns:
            str: 保存的 JSON 文件路径。
        """
        del base_path  # 不再保存 .py 模块，避免生成代码的执行面
        agent_name = cls.sanitize_name(config["name"])
        return _save_custom_agent_json(config, agent_name)

    @classmethod
    def generate_complex_prompt(cls, agent_type: str, specialization: str) -> str:
        """根据智能体类型和专长生成复杂的系统提示词模板。

        agent_type: "security" / "development" / "research"
        """
        prompts = {
            "security": f"""# {specialization} 安全智能体

你是白泽安全智能平台的高级安全专家，专精于 {specialization.lower()}。

## 核心能力
- **技术深度**: 精通安全工具、技术与流程
- **分析思维**: 分析复杂系统并识别漏洞
- **战略规划**: 制定综合安全评估策略
- **风险评估**: 评估并排序安全风险
- **文档输出**: 创建详细、可操作的安全报告

## 主要目标
1. **漏洞发现**: 系统性地发现安全弱点
2. **风险分析**: 评估已发现漏洞的潜在影响
3. **漏洞验证**: 经授权后安全地验证漏洞
4. **修复指导**: 提供清晰、可操作的修复方案
5. **合规验证**: 确保符合安全标准

## 输出标准
所有发现必须包含:
- **风险等级**: 严重/高/中/低
- **描述**: 漏洞的清晰解释
- **证据**: 截图、日志或代码片段
- **影响**: 业务和技术后果
- **修复**: 逐步修复指令
- **参考**: CVE 编号、公告链接等""",

            "development": f"""# {specialization} 开发智能体

你是白泽安全智能平台的高级开发专家，专注于 {specialization.lower()}。

## 核心能力
- **架构设计**: 构建可扩展、可维护的系统架构
- **代码卓越**: 编写干净、高效、文档完备的代码
- **安全优先**: 默认实现安全编码实践
- **性能优化**: 构建高性能解决方案

## 最佳实践
- 有效使用版本控制
- Code Review 所有更改
- 记录架构决策
- 实现适当的日志和监控""",

            "research": f"""# {specialization} 研究智能体

你是白泽安全智能平台的专业研究分析师，专注于 {specialization.lower()}。

## 研究能力
- **数据收集**: 从多个来源收集信息
- **分析**: 深度分析复杂数据
- **综合**: 将发现整合为可操作见解
- **验证**: 交叉验证信息

## 质量标准
- 准确性: 验证所有事实和数据
- 客观性: 呈现均衡观点
- 清晰性: 使用清晰简洁语言
- 相关性: 聚焦可操作见解""",
        }
        return prompts.get(agent_type, prompts["security"])


def _save_custom_agent_json(config: dict[str, Any], agent_name: str) -> str:
    """将智能体保存为自定义智能体 JSON 记录，使前端通过
    CustomAgentStore 立即可见，无需重启服务。

    同名智能体（按 name 匹配）已存在时复用其 id 与 created_at（upsert），
    避免重复堆积。返回保存的 JSON 文件路径。
    """
    custom_dir = os.path.join(os.path.expanduser("~"), ".baize", "custom", "agents")
    os.makedirs(custom_dir, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    display_name = config.get("display_name", config["name"])

    # 同名 upsert：复用已有 id，保留 created_at
    existing_id = None
    existing_created = None
    for f in os.listdir(custom_dir):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(custom_dir, f), encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("name") == config["name"]:
                existing_id = data.get("id")
                existing_created = data.get("created_at")
                break
        except (json.JSONDecodeError, OSError):
            continue

    agent_id = existing_id or secrets.token_hex(10)
    agent_json = {
        "id": agent_id,
        "name": config["name"],
        "display_name": display_name,
        "description": config.get("description", ""),
        "instructions": config.get("system_prompt", ""),
        "model": config.get("model", ""),
        "tools": config.get("tools", []),
        "created_at": existing_created or now,
        "updated_at": now,
        "is_custom": True,
    }

    json_path = os.path.join(custom_dir, f"{agent_id}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(agent_json, f, indent=2, ensure_ascii=False)

    return json_path


# ===========================================================================
#  工具函数 — 供 Agent 实例使用
# ===========================================================================

def _tool_list_available_tools() -> str:
    """列出白泽平台可用的全部工具及其用途。"""
    tools_info = {}
    for tool_id, tool_data in AgentBuilder.AVAILABLE_TOOLS_INFO.items():
        tools_info[tool_id] = {
            "name": tool_data["name"],
            "description": tool_data["description"],
        }
    return json.dumps(tools_info, ensure_ascii=False, indent=2)


def _tool_generate_agent_code(config_json: str) -> str:
    """根据配置生成智能体 Python 代码。

    config_json: {name, description, system_prompt, tools: [str]}
    """
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as exc:
        return f"配置解析失败: {exc}"

    for key in ("name", "description", "system_prompt"):
        if key not in config:
            return f"缺少必填字段: {key}"

    if "tools" not in config:
        config["tools"] = []

    return AgentBuilder.build_agent_file(config)


def _tool_save_agent_file(config_json: str) -> str:
    """生成并保存智能体为自定义智能体 JSON（~/.baize/custom/agents/）。"""
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as exc:
        return f"配置解析失败: {exc}"

    for key in ("name", "description", "system_prompt"):
        if key not in config:
            return f"缺少必填字段: {key}"

    try:
        filepath = AgentBuilder.save_agent_file(config)
        agent_key = AgentBuilder.sanitize_name(config["name"])
        return (
            f"智能体已保存为自定义智能体，立即可用，无需重启服务:\n"
            f"- 配置文件: {filepath}\n"
            f"- 显示名称: {config['name']}\n"
            f"- 注册键名: {agent_key}\n"
            f"- 现在即可在智能体列表/会话中选用。"
        )
    except Exception as exc:
        return f"保存失败: {exc}"


def _tool_generate_system_prompt(agent_type: str, specialization: str) -> str:
    """为指定类型生成系统提示词模板。

    agent_type: "security" / "development" / "research"
    """
    return AgentBuilder.generate_complex_prompt(agent_type, specialization)


# ===========================================================================
#  AGENT_BUILDER_TOOLS — 工具元数据列表
# ===========================================================================

_AGENT_BUILDER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_available_tools",
        "description": "列出白泽平台可用的全部工具及其描述，供选择新智能体的工具集。",
        "func": _tool_list_available_tools,
    },
    {
        "name": "generate_agent_code",
        "description": (
            "根据配置生成完整的智能体 Python 模块代码。"
            "config_json 为 JSON 字符串，格式: "
            '{"name":"智能体名","description":"描述","system_prompt":"提示词内容","tools":["tool_name"]}'
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "config_json": {
                    "type": "string",
                    "description": "包含 name/description/system_prompt/tools 的 JSON 配置",
                },
            },
            "required": ["config_json"],
        },
        "func": _tool_generate_agent_code,
    },
    {
        "name": "save_agent_file",
        "description": (
            "将生成的智能体保存为自定义智能体（写入 ~/.baize/custom/agents/ 下的 JSON 配置），"
            "保存后立即可在智能体列表中使用，无需重启服务。"
            "config_json 格式与 generate_agent_code 相同。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "config_json": {"type": "string", "description": "智能体配置 JSON"},
            },
            "required": ["config_json"],
        },
        "func": _tool_save_agent_file,
    },
    {
        "name": "generate_system_prompt",
        "description": (
            "为指定类型的智能体生成完整的系统提示词模板。"
            "agent_type: security/development/research"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "description": "类型: security/development/research",
                },
                "specialization": {"type": "string", "description": "专长领域描述"},
            },
            "required": ["agent_type", "specialization"],
        },
        "func": _tool_generate_system_prompt,
    },
]


def _build_agent_builder_tools() -> list[AgentTool]:
    """将 _AGENT_BUILDER_TOOLS 列表转换为 AgentTool 列表。"""
    tools: list[AgentTool] = []
    for item in _AGENT_BUILDER_TOOLS:
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

AGENT_KEY = "agent_builder"
_instructions = get_agent_instructions(AGENT_KEY)
_lines = _instructions.split("\n", 2)
_display_name = _lines[0].lstrip("# ").strip() if _lines else "Agent Builder"
_display_desc = "白泽元智能体 — 根据用户需求自动分析与生成新的安全智能体模块，支持完整代码产出与热加载"

agent_builder = Agent(
    name=_display_name or "Agent Builder",
    description=_display_desc,
    instructions=_instructions,
    model=None,
    tools=_build_agent_builder_tools(),
)
