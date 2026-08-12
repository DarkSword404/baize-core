"""
Confirm 节点执行器 — 人工确认点（仅 manual 管道）。

利用 LangGraph interrupt() 机制暂停图执行，等待外部 Command 注入。
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


class ConfirmNodeExecutor(BaseNodeExecutor):
    """人工确认节点 — 暂停执行，等待安全分析师判决。"""

    node_type = "confirm"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        try:
            # 渲染确认提示
            prompt = self._render_template(node.confirm_prompt, state)

            # 调用 LangGraph interrupt() 暂停执行
            # 这会抛出 GraphInterrupt 异常，由 Compiler 层适配
            human_choice = interrupt({
                "confirm_node_id": node.id,
                "prompt": prompt,
                "options": node.confirm_options,
            })

            # interrupt() 返回后：
            # human_choice 是外部 Command(resume=...) 传入的值
            choice = str(human_choice)

            # 根据选择决定路由
            route_to = node.confirm_branches.get(choice, node.confirm_branches.get("approve", ""))

            updates.update(self._record_done(
                node, state,
                f"人工选择: {choice} → {route_to}",
                {"choice": choice, "route": route_to},
            ))
            updates["human_response"] = choice
            updates["route"] = route_to

        except Exception as e:
            logger.exception(f"Confirm 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates
