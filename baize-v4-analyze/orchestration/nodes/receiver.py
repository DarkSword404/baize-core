"""
Receiver 节点执行器 — 获取一条告警作为流水线输入。

两种数据来源（对应 ALERT-TRIAGE-SILENT-SESSION.md §4.5）：
1. 会话直接注入：context 携带 `_inbox_seq`（长驻 Supervisor 已从持久收件箱
   claim 的告警序号）→ 直接从收件箱读取，不经过内存队列；
2. 回退：从绑定接收器内存队列 consume（兼容手动 run / 调试场景）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


def _decode_payload(raw: bytes, content_type: str) -> str:
    """解码 payload 为可展示文本。"""
    if content_type in ("pdf", "binary"):
        return f"[二进制数据, {len(raw or b'')} bytes]"
    try:
        return (raw or b"").decode("utf-8", errors="replace")
    except Exception:
        return f"[解码失败, {len(raw or b'')} bytes]"


class ReceiverNodeExecutor(BaseNodeExecutor):
    """从收件箱（会话模式）或接收器队列拉取数据。"""

    node_type = "receiver"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        try:
            # ---- 1. 会话直接注入：收件箱（唯一事实源） ----
            item = self._resolve_inbox_item(state)
            if item is not None:
                updates.update(self._record_item(node, state, item))
                # 每条入站数据 = 一条流水线对话的起点
                updates["dialog"] = [
                    self._dialog_entry(node, item, source="receiver_session")
                ]
                updates["route"] = ""
                return updates

            # ---- 2. 回退：内存队列（兼容手动/调试） ----
            updates.update(await self._consume_queue(node, state))
            updates["route"] = ""
            return updates

        except Exception as e:
            logger.exception(f"Receiver 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""
        return updates

    # ------------------------------------------------------------------
    # 来源 1：收件箱注入
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_inbox_item(state: PipelineState) -> dict[str, Any] | None:
        context = state.get("context") or {}
        seq = context.get("_inbox_seq")
        if seq is None:
            return None
        try:
            from baize.receivers.inbox import get_alert_inbox
            item = get_alert_inbox().get(int(seq), with_payload=True)
        except Exception:
            logger.exception("读取收件箱告警失败 seq=%s", seq)
            return None
        if item is None:
            logger.warning("收件箱告警不存在或已被清理 seq=%s", seq)
        return item

    def _record_item(
        self,
        node: PipelineNode,
        state: PipelineState,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        """把收件箱条目转成与队列消费一致的节点输出。"""
        raw = item.get("raw_payload") or b""
        payload_str = _decode_payload(raw, item.get("content_type") or "")
        output = {
            "receiver_id": item.get("receiver_id", ""),
            "timestamp": item.get("received_at") or item.get("timestamp") or 0.0,
            "source": item.get("source", ""),
            "content_type": item.get("content_type", ""),
            "payload": payload_str,
            "payload_size": item.get("payload_size", len(raw)),
            "metadata": item.get("metadata") or {},
            "seq": item.get("seq"),
        }
        return self._record_done(node, state, payload_str, output)

    @staticmethod
    def _dialog_entry(
        node: PipelineNode,
        item: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        """把入站数据转成对话起点条目（每次入站 = 一条流水线对话）。"""
        raw = item.get("raw_payload") or b""
        payload_str = _decode_payload(raw, item.get("content_type") or "")
        return {
            "kind": "input",
            "node": node.id,
            "node_type": node.type,
            "source": source,
            "receiver_id": item.get("receiver_id", ""),
            "content_type": item.get("content_type", ""),
            "content": payload_str,
            "seq": item.get("seq"),
            "received_at": item.get("received_at"),
            "timestamp": time.time(),
        }

    @staticmethod
    def _entry_from_queue(node: PipelineNode, data: Any) -> dict[str, Any]:
        """把队列数据转成对话起点条目。"""
        payload_str = _decode_payload(
            getattr(data, "raw_payload", b"") or b"",
            getattr(data, "content_type", "") or "",
        )
        return {
            "kind": "input",
            "node": node.id,
            "node_type": node.type,
            "source": "queue",
            "receiver_id": getattr(data, "receiver_id", ""),
            "content_type": getattr(data, "content_type", ""),
            "content": payload_str,
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------
    # 来源 2：内存队列（回退）
    # ------------------------------------------------------------------

    async def _consume_queue(
        self,
        node: PipelineNode,
        state: PipelineState,
    ) -> dict[str, Any]:
        from baize.receivers.manager import ReceiverManager
        mgr = ReceiverManager.get()

        # node.agent 用作 receiver_id；会话绑定优先取 context
        context = state.get("context") or {}
        receiver_id = (
            context.get("_receiver_id")
            or node.agent
            or node.id
        )
        data = await mgr.consume(receiver_id, max_wait=30.0)

        if data is None:
            # 队列为空
            return self._record_done(
                node, state,
                output="等待数据超时，队列为空",
                data={"status": "empty_queue"},
            )

        raw = data.raw_payload or b""
        payload_str = _decode_payload(raw, data.content_type)
        output = {
            "receiver_id": data.receiver_id,
            "timestamp": data.timestamp,
            "source": data.source,
            "content_type": data.content_type,
            "payload": payload_str,
            "payload_size": len(raw),
            "metadata": data.metadata,
        }
        updates = self._record_done(node, state, payload_str, output)
        # 队列消费成功 = 一条流水线对话的起点
        updates["dialog"] = [self._entry_from_queue(node, data)]
        return updates
