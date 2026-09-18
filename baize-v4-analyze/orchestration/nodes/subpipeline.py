"""
Subpipeline 节点执行器 — 嵌套子流水线。
"""

from __future__ import annotations

import logging
from typing import Any

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


class SubpipelineNodeExecutor(BaseNodeExecutor):
    """嵌套子流水线 — 将一组子节点作为独立子图执行。"""

    node_type = "subpipeline"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        try:
            if not node.sub_nodes:
                updates.update(self._record_done(node, state, "无子节点", {}))
                updates["route"] = ""
                return updates

            from baize.orchestration.nodes.parallel import get_executor

            # 顺序执行子节点
            merged_nodes = dict(state.get("nodes", {}))
            final_output = ""
            final_data: dict[str, Any] = {}

            for child_node in node.sub_nodes:
                executor = get_executor(child_node.type)
                # 构造子状态（继承父状态上下文）
                child_state: PipelineState = {
                    **state,
                    "current_node": child_node.id,
                    "route": "",
                }
                result = await executor.execute(child_node, child_state)
                if isinstance(result, dict):
                    merged_nodes.update(result.get("nodes", {}))
                    final_output = result.get("output", final_output) or final_output
                    final_data.update(result.get("data", final_data) or {})

            updates.update(self._record_done(node, state, final_output, final_data))
            updates["nodes"] = merged_nodes
            updates["route"] = ""

        except Exception as e:
            logger.exception(f"Subpipeline 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates
