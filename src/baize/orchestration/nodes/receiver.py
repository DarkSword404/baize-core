"""
Receiver 节点执行器 — 从数据接收器队列拉取数据作为流水线输入。
"""

from __future__ import annotations

import logging
from typing import Any

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


class ReceiverNodeExecutor(BaseNodeExecutor):
    """从绑定接收器队列拉取数据。"""

    node_type = "receiver"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        try:
            from baize.receivers.manager import ReceiverManager
            mgr = ReceiverManager.get()

            # node.agent 用作 receiver_id
            receiver_id = node.agent or node.id
            data = await mgr.consume(receiver_id, max_wait=30.0)

            if data is None:
                # 队列为空
                updates.update(self._record_done(
                    node, state,
                    output="等待数据超时，队列为空",
                    data={"status": "empty_queue"},
                ))
                updates["route"] = ""
                return updates

            # 解码 payload
            payload_str = ""
            if data.content_type in ("pdf", "binary"):
                payload_str = f"[二进制数据, {len(data.raw_payload)} bytes]"
            else:
                try:
                    payload_str = data.raw_payload.decode("utf-8", errors="replace")
                except Exception:
                    payload_str = f"[解码失败, {len(data.raw_payload)} bytes]"

            output = {
                "receiver_id": data.receiver_id,
                "timestamp": data.timestamp,
                "source": data.source,
                "content_type": data.content_type,
                "payload": payload_str,
                "payload_size": len(data.raw_payload),
                "metadata": data.metadata,
            }

            updates.update(self._record_done(
                node, state,
                output=payload_str,
                data=output,
            ))
            updates["route"] = ""

        except Exception as e:
            logger.exception(f"Receiver 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates
