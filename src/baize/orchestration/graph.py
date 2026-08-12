"""
流水线图编译器 — 将 YAML/JSON 流水线定义编译为 LangGraph StateGraph。

安全人员只需编写声明式 YAML 配置，编译器负责生成可执行的图。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from baize.orchestration.state import PipelineState


# ---------------------------------------------------------------------------
# YAML 流水线下发格式
# ---------------------------------------------------------------------------
# steps:
#   - id: step_1
#     agent: triage                  # 使用哪个 Agent
#     prompt: "分析 {{ context.src_ip }}"   # 提示词，支持 {{ }} 模板
#     timeout: 300                   # 超时 (秒)
#
#   - id: decision_1
#     type: condition                # 条件分支
#     branches:
#       - when: "{{ steps.step_1.data.threat_score > 0.8 }}"
#         goto: block_ip
#       - default: done
#
#   - id: block_ip
#     agent: response
#     tools: [block_ip_on_firewall]
#     prompt: "封禁 IP {{ context.src_ip }}"
#
#   - id: confirm
#     type: human                    # 人工确认点
#     prompt: "威胁评分 {{ steps.step_1.data.threat_score }}，是否封禁？"
#     choices: [approve, reject]
#     branches:
#       approve: block_ip
#       reject: done
#
#   - id: done
#     type: end
# ---------------------------------------------------------------------------


class PipelineGraphCompiler:
    """将流水线定义 (dict) 编译为 LangGraph StateGraph。"""

    def __init__(self, node_factory: Callable):
        """*node_factory*: (step_def: dict) -> Callable[[PipelineState], PipelineState]

        返回一个可被 LangGraph.add_node 使用的节点函数。
        """
        self._factory = node_factory

    def compile(self, pipeline_def: dict) -> StateGraph:
        """编译流水线并返回 compiled StateGraph。"""
        graph = StateGraph(PipelineState)

        steps = pipeline_def.get("steps", [])
        step_ids: list[str] = []
        condition_nodes: dict[str, dict] = {}
        end_nodes: set[str] = set()

        for step in steps:
            sid = step.get("id", "")
            if not sid:
                continue

            step_type = step.get("type", "agent")

            if step_type == "end":
                end_nodes.add(sid)
                continue

            if step_type == "condition":
                condition_nodes[sid] = step
                continue

            # 普通步骤 (agent / human)
            node_fn = self._factory(step)
            graph.add_node(sid, node_fn)
            step_ids.append(sid)

        # 添加条件分支节点
        for cid, cstep in condition_nodes.items():
            condition_ids = set()
            for branch in cstep.get("branches", []):
                target = branch.get("goto", "")
                if target:
                    condition_ids.add(target)

            graph.add_node(cid, self._factory(cstep))

        # 构建边：顺序连接 + 条件分支
        for i, sid in enumerate(step_ids):
            next_sid = step_ids[i + 1] if i + 1 < len(step_ids) else None

            # 如果下一个是条件节点，用条件边连接
            if next_sid and next_sid in condition_nodes:
                cstep = condition_nodes[next_sid]
                conditions = {}
                for branch in cstep.get("branches", []):
                    target = branch.get("goto", "")
                    when = branch.get("when", "")
                    if target and when:
                        conditions[target] = when
                    elif target and "default" in branch:
                        conditions[target] = "default"
                    elif target and branch.get("default"):
                        conditions[target] = "default"

                # 生成条件路由函数
                graph.add_conditional_edges(
                    sid,
                    _make_router(next_sid, conditions),
                    {k: k for k in conditions.keys()},
                )
                # 条件节点本身按条件路由后的终点继续
                for target in conditions.keys():
                    # 找到 target 后面的节点
                    try:
                        target_idx = step_ids.index(target)
                        end_target = step_ids[target_idx + 1] if target_idx + 1 < len(step_ids) else END
                        if end_target not in condition_nodes and end_target not in end_nodes:
                            graph.add_edge(target, end_target)
                        elif end_target == END or end_target in end_nodes:
                            graph.add_edge(target, END)
                    except ValueError:
                        graph.add_edge(target, END)

            elif next_sid and next_sid in end_nodes:
                graph.add_edge(sid, END)
            elif next_sid:
                graph.add_edge(sid, next_sid)
            else:
                graph.add_edge(sid, END)

        # 设置入口
        if step_ids:
            graph.set_entry_point(step_ids[0])

        # 使用内存 checkpoint 支持人工中断恢复
        memory = MemorySaver()
        return graph.compile(checkpointer=memory)


def _make_router(decision_node_id: str, conditions: dict[str, str]) -> Callable:
    """生成 LangGraph 条件路由函数。

    根据上一步的输出（PipelineState.route 或 data 中的条件匹配）决定下一步。
    """

    def router(state: PipelineState) -> str:
        route = state.get("route", "")
        if route and route in conditions:
            return route

        # 尝试从 steps 数据中匹配条件表达式
        for step_id, step_state in state.get("steps", {}).items():
            data = step_state.get("data", {}) or {}
            for target, expr in conditions.items():
                if expr == "default":
                    continue
                if _eval_expression(expr, data, state):
                    return target

        # 找第一个 default
        for target, expr in conditions.items():
            if expr == "default":
                return target

        # 全不匹配，返回第一个
        return next(iter(conditions.keys()), END)

    return router


# ---------------------------------------------------------------------------
# 简单表达式求值（安全子集，无 exec/eval）
# ---------------------------------------------------------------------------
_SAFE_EXPR = re.compile(
    r"^\s*([\w\.]+)\s*([><=!]+)\s*([\w\.\-]+)\s*$"
)


def _eval_expression(expr: str, data: dict, state: PipelineState) -> bool:
    """安全求值简单布尔表达式：data.threat_score > 0.8"""
    m = _SAFE_EXPR.match(expr)
    if not m:
        return False

    left = _resolve_path(m.group(1).strip(), data, state)
    op = m.group(2).strip()
    right_str = m.group(3).strip()

    # 尝试转换右值为数字
    try:
        right = float(right_str)
    except ValueError:
        right = right_str.strip("'\"")

    if isinstance(right, float) and isinstance(left, (int, float)):
        pass
    elif isinstance(right, float):
        try:
            left = float(left)
        except (TypeError, ValueError):
            return False

    if op == ">":
        return left > right
    elif op == ">=":
        return left >= right
    elif op == "<":
        return left < right
    elif op == "<=":
        return left <= right
    elif op == "==":
        return left == right
    elif op == "!=":
        return left != right

    return False


# ---------------------------------------------------------------------------
# 简单路径解析: "steps.triage.data.threat_score" → 取值
# ---------------------------------------------------------------------------
def resolve_template(template: str, state: PipelineState) -> str:
    """解析 {{ }}} 模板语法，将占位符替换为实际值。"""
    def _replacer(m: re.Match) -> str:
        path = m.group(1).strip()
        resolved = _resolve_path(path, {}, state)
        return str(resolved)

    return re.sub(r"\{\{\s*(.*?)\s*\}\}", _replacer, template)


def _resolve_path(path: str, data: dict, state: PipelineState) -> Any:
    """沿路径取值：context.src_ip / steps.triage.data.threat_score"""
    parts = path.split(".", 1)
    root = parts[0]

    if root == "context":
        if len(parts) > 1:
            return _deep_get(state.get("context", {}) or {}, parts[1])
        return state.get("context", {})

    if root == "steps":
        if len(parts) > 1:
            rest = parts[1]
            step_parts = rest.split(".", 2)
            step_id = step_parts[0]
            step_state = state.get("steps", {}).get(step_id, {}) or {}
            if len(step_parts) == 1:
                return step_state
            sub_path = step_parts[1]
            # 先查 data，再查顶级
            result = _deep_get(step_state.get("data", {}) or {}, sub_path)
            if result is not None:
                return result
            return _deep_get(step_state, sub_path)
        return state.get("steps", {})

    # 直接查 data
    return _deep_get(data, path)


def _deep_get(d: dict, path: str, default: Any = "") -> Any:
    """逐级从字典中取值。"""
    keys = path.split(".")
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, default)
        else:
            return default
    return d
