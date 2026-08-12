"""
白泽编排竞赛工具 — 供 Orchestration Agent 调度专项智能体使用

提供三个核心编排工具:
1. ``run_specialist`` — 单个专项智能体执行
2. ``run_dual_approach_contest`` — 双路并行探索竞赛（正交策略比较）
3. ``run_parallel_specialists`` — 2–4 个智能体并行执行独立子任务

所有工具将 worker 输出包裹在 ``<orchestrator_internal>`` 标记中，
前端/用户不会看到 worker 的原始输出，只有编排智能体的最终合成结果对外展示。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from baize.sdk.agent import Agent, AgentTool


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_DEFAULT_WORKER_MAX_TURNS: int = 6
_MAX_WORKER_OUTPUT_CHARS: int = 4000
_COMBINED_OUTPUT_BUDGET: int = 8500

_INTERNAL_OPEN: str = (
    "<orchestrator_internal>\n"
    "# 编排内部数据 — 仅供编排智能体阅读\n"
    "# 不要向用户引用/复制/转述以下内容\n"
    "# 阅读后自行决策，用你自己的表达写最终回复\n"
)
_INTERNAL_CLOSE: str = "</orchestrator_internal>"

_WORKER_CONSTRAINTS: str = (
    "## 执行约束（必须遵守）\n"
    f"- 本回合最多 {_DEFAULT_WORKER_MAX_TURNS} 步工具调用\n"
    "- 每步最多使用一个工具\n"
    "- 严格按照下文的 framing 执行，不要自行扩展范围\n\n"
    "## 输出约束\n"
    "- 返回简洁的执行摘要，不是最终用户报告\n"
    "- 结构: 状态、关键发现、风险/未知、建议下一步\n"
    "- 保持简短，编排智能体会负责综合结论\n\n"
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _wrap_internal(body: str) -> str:
    return f"{_INTERNAL_OPEN}\n{body}\n{_INTERNAL_CLOSE}"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n... [截断 {len(text) - max_chars} 字符] ...\n\n{text[-half:]}"


def _new_group_id(kind: str) -> str:
    return f"{kind}:{uuid.uuid4().hex[:12]}"


def _resolve_agent(agent_type: str) -> tuple[Agent | None, str | None]:
    """按键名查找智能体。

    Returns:
        (agent, error_message) — 成功时 error_message 为 None。
    """
    from baize.agents import _AGENTS

    key = agent_type.strip()
    if key in _AGENTS:
        return _AGENTS[key], None

    # 尝试按 display_name 模糊匹配
    for k, a in _AGENTS.items():
        if a.name.strip().lower() == key.lower():
            return a, None

    available = ", ".join(sorted(_AGENTS.keys()))
    return None, f"未找到智能体 '{key}'。可用: {available}"


def _resolve_and_filter_tools(agent: Agent, allowed_tool_name: str) -> tuple[
    list[AgentTool] | None, str | None
]:
    """筛选 worker 可以使用的工具。

    Returns:
        (filtered_tools, error_message) — 成功时 error_message 为 None。
    """
    requested = (allowed_tool_name or "").strip()
    if requested.lower() in ("", "none", "no_tool", "no-tool", "reasoning_only"):
        return [], None

    requested_names = [n.strip() for n in requested.split(",") if n.strip()]
    if not requested_names:
        return [], None

    by_name: dict[str, AgentTool] = {t.name: t for t in (agent.tools or [])}
    resolved: list[AgentTool] = []
    missing: list[str] = []

    for name in requested_names:
        tool = by_name.get(name)
        if tool is None:
            missing.append(name)
        elif tool not in resolved:
            resolved.append(tool)

    if missing:
        avail = ", ".join(sorted(by_name.keys()))
        return None, f"工具 {', '.join(f'`{m}`' for m in missing)} 不在 `{agent.name}` 可用列表中。可用: {avail}"

    return resolved, None


def _build_worker_input(
    label: str,
    framing: str,
    user_task: str,
    rationale: str,
    allowed_tool_name: str,
) -> str:
    return (
        f"{_WORKER_CONSTRAINTS}"
        f"## 执行方向 ({label})\n{framing}\n\n"
        f"## 用户任务\n{user_task}\n\n"
        f"## 可用工具\n{allowed_tool_name or '无（纯推理模式）'}\n\n"
        f"## 执行理由（编排智能体决定）\n{rationale}\n"
    )


async def _run_worker(
    agent_key: str,
    agent_display_name: str,
    allowed_tool_name: str,
    framing: str,
    user_task: str,
    rationale: str,
    label: str,
    max_output_chars: int = _MAX_WORKER_OUTPUT_CHARS,
) -> dict[str, str]:
    """执行单个 worker 并返回结果字典。"""
    base_agent, error = _resolve_agent(agent_key)
    if error or base_agent is None:
        return {
            "label": label,
            "agent_name": agent_display_name,
            "allowed_tool": allowed_tool_name,
            "status": "failed",
            "output": error or "未知错误",
        }

    filtered_tools, tool_error = _resolve_and_filter_tools(base_agent, allowed_tool_name)
    if tool_error:
        return {
            "label": label,
            "agent_name": base_agent.name,
            "allowed_tool": allowed_tool_name,
            "status": "failed",
            "output": tool_error,
        }

    # 创建 worker agent（相同指令，限制工具集）
    worker = Agent(
        name=base_agent.name,
        description=base_agent.description,
        instructions=base_agent.instructions,
        model=base_agent.model,
        tools=filtered_tools or [],
        max_tool_calls=_DEFAULT_WORKER_MAX_TURNS,
    )

    user_input = _build_worker_input(label, framing, user_task, rationale, allowed_tool_name)

    try:
        result = await worker.run(user_input)
        output = str(result.final_output)
    except Exception as exc:
        return {
            "label": label,
            "agent_name": base_agent.name,
            "allowed_tool": allowed_tool_name,
            "status": "failed",
            "output": f"{type(exc).__name__}: {exc}",
        }

    if not output.strip():
        output = "(无文本输出)"

    return {
        "label": label,
        "agent_name": base_agent.name,
        "allowed_tool": allowed_tool_name,
        "status": "completed",
        "output": _truncate(output, max_output_chars),
    }


def _compose_brief(
    title: str,
    overall_status: str,
    rationale: str,
    results: list[dict],
    decision_text: str,
) -> str:
    lines = [
        f"## {title}",
        "",
        f"- 整体状态: `{overall_status}`",
        f"- 理由: {rationale}",
        "",
    ]
    for r in results:
        lines.append(f"### Worker {r['label']}")
        lines.append(f"- 智能体: `{r['agent_name']}`")
        lines.append(f"- 工具: `{r.get('allowed_tool', 'none')}`")
        lines.append(f"- 状态: `{r['status']}`")
        lines.append("")
        lines.append("#### 执行摘要")
        lines.append(str(r.get("output", "")))
        lines.append("")

    lines.append("### 下一步决策")
    lines.append(decision_text)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 公开工具函数
# ---------------------------------------------------------------------------

async def tool_run_specialist(
    agent_type: str,
    allowed_tool_name: str,
    task: str,
    framing: str,
) -> str:
    """运行单个专项智能体，编排智能体保持控制。

    用于竞赛后的获胜路径、窄范围跟进调查、或只需单路执行的场景。
    如果前端需要多路并行探索，使用 ``tool_run_parallel_specialists`` 或
    ``tool_run_dual_approach_contest``。

    Args:
        agent_type: 智能体注册键名 (如 ``red_teamer``)
        allowed_tool_name: worker 可使用的工具名，逗号分隔可授予小工具箱，
           或 ``none`` 表示纯推理模式。
        task: 具体工作任务（避免原文照搬用户完整简报）
        framing: 执行策略和约束
    """
    result = await _run_worker(
        agent_key=agent_type,
        agent_display_name=agent_type,
        allowed_tool_name=allowed_tool_name,
        framing=framing,
        user_task=task,
        rationale="编排智能体指定的单路执行",
        label="S",
    )

    brief = _compose_brief(
        title="专项执行摘要",
        overall_status=result["status"],
        rationale="编排智能体选择的单路执行",
        results=[result],
        decision_text="编排智能体根据上述摘要决定下一步操作（继续调用工具或给出最终结论）。",
    )
    return _wrap_internal(brief)


async def tool_run_dual_approach_contest(
    agent_type_for_approach_a: str,
    agent_type_for_approach_b: str,
    allowed_tool_for_approach_a: str,
    allowed_tool_for_approach_b: str,
    approach_a_framing: str,
    approach_b_framing: str,
    shared_user_task: str,
    contest_rationale: str,
) -> str:
    """双路并行探索竞赛 — 两路策略在同一任务上的并行比较。

    用于以下场景: 正交方法论比较、竞争假设、高风险分叉决策前试水。

    **worker A/B 并发执行，互不干扰。**

    Args:
        agent_type_for_approach_a: 策略A的智能体键名
        agent_type_for_approach_b: 策略B的智能体键名
        allowed_tool_for_approach_a: 策略A可用工具
        allowed_tool_for_approach_b: 策略B可用工具
        approach_a_framing: 策略A怎么解决问题的技术路线描述
        approach_b_framing: 策略B怎么解决问题的技术路线描述（应与A正交）
        shared_user_task: 两路共享的用户任务
        contest_rationale: 为什么要进行竞赛的简短理由
    """
    results = await asyncio.gather(
        _run_worker(
            agent_key=agent_type_for_approach_a,
            agent_display_name=agent_type_for_approach_a,
            allowed_tool_name=allowed_tool_for_approach_a,
            framing=approach_a_framing,
            user_task=shared_user_task,
            rationale=contest_rationale,
            label="A",
            max_output_chars=_COMBINED_OUTPUT_BUDGET // 2,
        ),
        _run_worker(
            agent_key=agent_type_for_approach_b,
            agent_display_name=agent_type_for_approach_b,
            allowed_tool_name=allowed_tool_for_approach_b,
            framing=approach_b_framing,
            user_task=shared_user_task,
            rationale=contest_rationale,
            label="B",
            max_output_chars=_COMBINED_OUTPUT_BUDGET // 2,
        ),
    )

    all_failed = all(r["status"] == "failed" for r in results)
    overall = "双路均失败" if all_failed else "等待编排决策"

    decision = (
        "双路均失败。编排智能体应简要说明阻塞原因并选择恢复路径。"
        if all_failed
        else "编排智能体比较两路证据、覆盖面和风险，决定下一步：用 ``run_specialist`` "
             "执行具体跟进，或再次发起竞赛比较新的选择。最终面向用户的结论由编排智能体生成。"
    )

    brief = _compose_brief(
        title="双路并行探索竞赛",
        overall_status=overall,
        rationale=contest_rationale,
        results=list(results),
        decision_text=decision,
    )
    return _wrap_internal(brief)


async def tool_run_parallel_specialists(
    workers_json: str,
    parallel_rationale: str,
) -> str:
    """2–4 个专项智能体并行执行独立子任务。

    用于 wave-1 并行广域侦察（多个正交战线的并行 scouting）或用户明确提出多个工作流时。

    ``workers_json`` 为 JSON 数组，每项含:
    - agent_type: 智能体键名
    - allowed_tool_name: 可用工具（或 ``none``）
    - task: 子任务描述
    - framing: 执行策略

    Args:
        workers_json: JSON 数组，2–4 个 worker 配置
        parallel_rationale: 为何需要并行执行的简短理由
    """
    raw = (workers_json or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"workers_json 不是合法 JSON: {exc}"

    if not isinstance(data, list):
        return "workers_json 必须是 JSON 数组"

    if len(data) < 2:
        return "至少 2 个 worker，或使用 run_specialist 处理单个任务"

    if len(data) > 4:
        return "最多 4 个并行 worker"

    required = ("agent_type", "allowed_tool_name", "task", "framing")
    for i, w in enumerate(data, start=1):
        if not isinstance(w, dict):
            return f"Worker {i} 必须是 JSON 对象"
        for key in required:
            if key not in w:
                return f"Worker {i} 缺少必填字段 `{key}`"

    per_cap = _COMBINED_OUTPUT_BUDGET // len(data)

    async def run_one(idx: int, w: dict):
        return await _run_worker(
            agent_key=str(w["agent_type"]),
            agent_display_name=str(w["agent_type"]),
            allowed_tool_name=str(w["allowed_tool_name"]),
            framing=str(w["framing"]),
            user_task=str(w["task"]),
            rationale=parallel_rationale,
            label=f"P{idx}",
            max_output_chars=per_cap,
        )

    results = list(await asyncio.gather(*(run_one(i + 1, w) for i, w in enumerate(data))))

    all_failed = all(r["status"] == "failed" for r in results)
    partial = any(r["status"] == "failed" for r in results)
    overall = "all_failed" if all_failed else ("partial" if partial else "completed")

    decision = (
        "全部并行 worker 失败。编排智能体应简要说明阻塞原因并选择恢复路径。"
        if all_failed
        else (
            "编排智能体合并 worker 摘要，在当前回合继续调用工具直至达成用户目标，"
            "或给出一次最终综合结论。"
        )
    )

    brief = _compose_brief(
        title="并行专项执行",
        overall_status=overall,
        rationale=parallel_rationale,
        results=results,
        decision_text=decision,
    )
    return _wrap_internal(brief)


# 工具元数据 — 供 Agent tools 参数直接引用
APPROACH_CONTEST_TOOLS: list[dict[str, Any]] = [
    {
        "name": "run_specialist",
        "description": (
            "运行单个专项智能体，编排智能体保持控制。"
            "用于竞赛后获胜路径跟进、窄范围调查、或只需单路执行的场景。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "description": "智能体注册键名，如 red_teamer、web_pentester 等",
                },
                "allowed_tool_name": {
                    "type": "string",
                    "description": "worker 可使用的工具名，逗号分隔可授予多个工具，或 'none' 纯推理模式",
                },
                "task": {
                    "type": "string",
                    "description": "具体工作任务（避免原文照搬用户完整简报）",
                },
                "framing": {
                    "type": "string",
                    "description": "执行策略和约束，包括是广域侦察还是窄范围跟进",
                },
            },
            "required": ["agent_type", "allowed_tool_name", "task", "framing"],
        },
        "handler": tool_run_specialist,
    },
    {
        "name": "run_dual_approach_contest",
        "description": (
            "双路并行探索竞赛 — 两路策略在同一任务上并行比较。"
            "用于正交方法论比较、竞争假设、高风险分叉决策前试水。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_type_for_approach_a": {"type": "string", "description": "策略A的智能体键名"},
                "agent_type_for_approach_b": {"type": "string", "description": "策略B的智能体键名"},
                "allowed_tool_for_approach_a": {"type": "string", "description": "策略A可用工具"},
                "allowed_tool_for_approach_b": {"type": "string", "description": "策略B可用工具"},
                "approach_a_framing": {"type": "string", "description": "策略A的技术路线描述"},
                "approach_b_framing": {"type": "string", "description": "策略B的技术路线描述（应与A正交）"},
                "shared_user_task": {"type": "string", "description": "两路共享的用户任务"},
                "contest_rationale": {"type": "string", "description": "为什么要进行竞赛的简短理由"},
            },
            "required": [
                "agent_type_for_approach_a",
                "agent_type_for_approach_b",
                "allowed_tool_for_approach_a",
                "allowed_tool_for_approach_b",
                "approach_a_framing",
                "approach_b_framing",
                "shared_user_task",
                "contest_rationale",
            ],
        },
        "handler": tool_run_dual_approach_contest,
    },
    {
        "name": "run_parallel_specialists",
        "description": (
            "2–4 个专项智能体并行执行独立子任务。"
            "用于 wave-1 并行广域侦察或用户明确提出多工作流时。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workers_json": {
                    "type": "string",
                    "description": "JSON 数组，每项含 agent_type/allowed_tool_name/task/framing",
                },
                "parallel_rationale": {
                    "type": "string",
                    "description": "为何需要并行执行的简短理由",
                },
            },
            "required": ["workers_json", "parallel_rationale"],
        },
        "handler": tool_run_parallel_specialists,
    },
]
