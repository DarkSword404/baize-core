"""
Decision 节点执行器 — 条件分支路由。
"""

from __future__ import annotations

import logging
from typing import Any

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


class DecisionNodeExecutor(BaseNodeExecutor):
    """条件分支路由 — 求值条件表达式，选择目标节点。"""

    node_type = "decision"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        route_to = ""
        try:
            for br in node.branches:
                if br.is_default:
                    route_to = br.target
                    continue
                if self._evaluate_condition(br.condition, state):
                    route_to = br.target
                    break

            if not route_to:
                raise RuntimeError(f"decision 节点 '{node.id}' 没有匹配到任何分支（也没有默认分支）")

            updates.update(self._record_done(node, state, f"branch → {route_to}", {"chosen": route_to}))
            updates["route"] = route_to

        except Exception as e:
            logger.exception(f"Decision 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates
