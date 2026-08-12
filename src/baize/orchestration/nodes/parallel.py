"""
Parallel 节点执行器 — 并发执行多个子节点。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from baize.orchestration.state import PipelineState, NodeRunRecord
from baize.orchestration.node_types import PipelineNode, ParallelBranch
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


class ParallelNodeExecutor(BaseNodeExecutor):
    """并行执行多个子分支 —— 借鉴 Prefect task.map() 和 LangGraph Send。"""

    node_type = "parallel"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        try:
            branches = node.parallel_branches
            executed: list[dict[str, Any]] = []

            async def run_single_branch(branch: ParallelBranch, branch_idx: int) -> dict[str, Any]:
                """执行单个并行分支。"""
                child_node = branch.node
                if child_node is None:
                    return {
                        "branch_id": branch.node_id,
                        "status": "skipped",
                        "reason": "no node definition",
                    }
                executor = get_executor(child_node.type)
                result = await executor.execute(child_node, state)
                return {
                    "branch_id": branch.node_id,
                    "node_id": child_node.id,
                    "status": "completed",
                    "result": result,
                }

            # 并发执行所有分支
            tasks = [run_single_branch(b, i) for i, b in enumerate(branches)]
            executed = await asyncio.gather(*tasks, return_exceptions=True)

            # 收集结果
            results = []
            for item in executed:
                if isinstance(item, Exception):
                    results.append({"status": "failed", "error": str(item)})
                else:
                    results.append(item)

            # 合并子节点的 nodes 记录
            nodes = dict(state.get("nodes", {}))
            for item in executed:
                if not isinstance(item, Exception) and item.get("node_id"):
                    result_updates = item.get("result", {})
                    if isinstance(result_updates, dict):
                        sub_nodes = result_updates.get("nodes", {})
                        nodes.update(sub_nodes)

            data = {"branches": results, "total": len(branches), "completed": len(results)}
            updates.update(self._record_done(node, state, f"{len(results)} 个分支执行完毕", data))
            updates["nodes"] = nodes
            updates["route"] = ""

        except Exception as e:
            logger.exception(f"Parallel 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates


def get_executor(node_type: str) -> BaseNodeExecutor:
    """根据节点类型获取对应的执行器实例（带缓存）。"""
    from baize.orchestration.nodes.agent import AgentNodeExecutor
    from baize.orchestration.nodes.decision import DecisionNodeExecutor
    from baize.orchestration.nodes.confirm import ConfirmNodeExecutor
    from baize.orchestration.nodes.transform import TransformNodeExecutor
    from baize.orchestration.nodes.subpipeline import SubpipelineNodeExecutor
    from baize.orchestration.nodes.receiver import ReceiverNodeExecutor
    from baize.orchestration.nodes.datatransformer import DataTransformerNodeExecutor

    _registry = {
        "agent": AgentNodeExecutor,
        "decision": DecisionNodeExecutor,
        "parallel": ParallelNodeExecutor,
        "confirm": ConfirmNodeExecutor,
        "transform": TransformNodeExecutor,
        "subpipeline": SubpipelineNodeExecutor,
        "receiver": ReceiverNodeExecutor,
        "datatransformer": DataTransformerNodeExecutor,
    }
    cls = _registry.get(node_type)
    if cls is None:
        raise ValueError(f"未知节点类型: {node_type}")
    return cls()
