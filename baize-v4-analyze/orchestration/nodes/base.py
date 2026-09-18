"""
节点执行器抽象基类。

每种节点类型对应一个子类，实现 execute() 方法。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode


class BaseNodeExecutor(ABC):
    """节点执行器抽象基类。

    子类必须实现 execute()，接收当前状态和节点定义，返回更新后的状态增量。
    """

    node_type: str = "base"

    @abstractmethod
    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        """执行节点逻辑，返回状态更新字典。

        Args:
            node: 当前节点定义
            state: 流水线当前状态

        Returns:
            dict: 更新到 PipelineState 的字段
        """
        ...

    def _record_start(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        """标记节点开始执行。"""
        import time
        return {
            "current_node": node.id,
            "nodes": {
                **state.get("nodes", {}),
                node.id: {
                    "node_id": node.id,
                    "node_type": node.type,
                    "status": "running",
                    "input": {
                        "context": state.get("context", {}),
                        "route": state.get("route", ""),
                    },
                    "started_at": time.time(),
                },
            },
        }

    def _record_done(self, node: PipelineNode, state: PipelineState, output: str, data: dict[str, Any]) -> dict[str, Any]:
        """标记节点完成。"""
        import time
        existing = dict(state.get("nodes", {}))
        existing[node.id] = {
            **existing.get(node.id, {}),
            "node_id": node.id,
            "node_type": node.type,
            "status": "completed",
            "output": output,
            "data": data,
            "ended_at": time.time(),
        }
        return {"nodes": existing}

    def _record_failed(self, node: PipelineNode, state: PipelineState, error: str) -> dict[str, Any]:
        """标记节点失败。"""
        import time
        existing = dict(state.get("nodes", {}))
        existing[node.id] = {
            **existing.get(node.id, {}),
            "node_id": node.id,
            "node_type": node.type,
            "status": "failed",
            "error": error,
            "ended_at": time.time(),
        }
        return {"nodes": existing}

    @staticmethod
    def _render_template(template: str, state: PipelineState) -> str:
        """渲染 Jinja2 模板，支持 {{ context.xxx }} 和 {{ steps.xxx.yyy }}。"""
        from jinja2 import Template
        nodes = state.get("nodes", {})
        ctx = {
            "context": state.get("context", {}),
            "steps": {nid: rec for nid, rec in nodes.items()},
            "state": {k: v for k, v in state.items() if k not in ("messages", "nodes", "context")},
        }
        try:
            return Template(template).render(**ctx)
        except Exception:
            # 回退：直接返回原模板
            return template

    @staticmethod
    def _evaluate_condition(expression: str, state: PipelineState) -> bool:
        """求值条件表达式（Jinja2 模板布尔化）。"""
        text = BaseNodeExecutor._render_template(expression, state)
        return text.strip().lower() in ("true", "1", "yes")
