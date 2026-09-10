"""
End 节点执行器 — 结束对话（流水线终点的对话处置节点）。

语义（对应当前"每一条入站数据 = 一条独立流水线对话"模型）：
- 流水线运行到该节点即宣告一次入站数据处理结束；
- 节点上的 ``save_dialog`` 决定本次对话的处置：
    * save_dialog=False（默认）→ dialog_action="discard"，run 终态时回收对话内容；
    * save_dialog=True           → dialog_action="save"，run 终态时保留完整对话归档。
执行器本身只做标记，实际回收/保留由 runner 在 run 终态统一执行。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


class EndNodeExecutor(BaseNodeExecutor):
    """结束对话节点 — 汇总本次对话并决定回收/保留。"""

    node_type = "end"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)
        try:
            dialog = list(state.get("dialog", []) or [])
            summary = _summarize_dialog(dialog)
            action = "save" if node.save_dialog else "discard"
            updates.update(self._record_done(
                node, state,
                output=f"结束对话，处置: {'保留归档' if action == 'save' else '回收删除'}",
                data={
                    "dialog_action": action,
                    "save_dialog": bool(node.save_dialog),
                    "dialog_turns": len(dialog),
                    "summary": summary,
                },
            ))
            updates["dialog_action"] = action
            updates["status"] = "completed"
            updates["route"] = ""
            # 已有有效 report（如终节点结论）时保留；否则以对话摘要兜底，
            # 保证对话被回收后 run 历史仍有可读摘要。
            if not (state.get("report") or ""):
                updates["report"] = summary
            return updates
        except Exception as e:
            logger.exception(f"End 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["dialog_action"] = "save"  # 异常时保守保留，避免误删对话
            updates["route"] = ""
            return updates


def _summarize_dialog(dialog: list[dict[str, Any]]) -> str:
    """把对话压缩为一段可读摘要（供 run 的 report 展示）。"""
    if not dialog:
        return "（本次处理未产生对话记录）"
    lines: list[str] = []
    for entry in dialog:
        kind = entry.get("kind", "")
        if kind == "input":
            content = str(entry.get("content", "")) or ""
            snippet = content[:200].replace("\n", " ")
            lines.append(f"[输入] {snippet}")
        elif kind == "llm":
            agent = entry.get("agent", "")
            output = str(entry.get("output", "")) or ""
            snippet = output[:200].replace("\n", " ")
            lines.append(f"[{agent}] {snippet}")
        elif kind == "note":
            lines.append(str(entry.get("content", "")))
    if not lines:
        lines.append("（对话已归档，摘要为空）")
    return "\n".join(lines)
