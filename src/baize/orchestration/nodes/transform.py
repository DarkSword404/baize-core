"""
Transform 节点执行器 — 数据转换/清洗（不调用 LLM）。
"""

from __future__ import annotations

import logging
from typing import Any

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


class TransformNodeExecutor(BaseNodeExecutor):
    """数据转换节点 — 对上下文进行结构化重组、过滤、聚合。"""

    node_type = "transform"

    TRANSFORMERS: dict[str, Any] = {
        "json_extract": lambda state: {"output": state.get("context", {})},
        "report_summary": lambda state: {"output": _generate_summary(state)},
        "enrich_ips": lambda state: {"output": _enrich_ips(state)},
    }

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        try:
            transformer = self.TRANSFORMERS.get(
                node.agent, self.TRANSFORMERS["json_extract"]
            )
            result = transformer(state)
            output_text = str(result.get("output", ""))
            data = result.get("output", {}) if isinstance(result.get("output"), dict) else {}

            updates.update(self._record_done(node, state, output_text, data))
            updates["route"] = ""

        except Exception as e:
            logger.exception(f"Transform 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates


def _generate_summary(state: PipelineState) -> dict:
    """从已完成节点生成摘要。"""
    nodes = state.get("nodes", {})
    completed = [f"  - {rid}: {rec.get('output', '')[:200]}" for rid, rec in nodes.items() if rec.get("status") == "completed"]
    return {"output": "执行摘要：\n" + "\n".join(completed)}


def _enrich_ips(state: PipelineState) -> dict:
    """IP 情报富化（占位，可对接外部威胁情报 API）。"""
    import re
    ctx = state.get("context", {})
    text = str(ctx)
    ips = list(set(re.findall(r'\d+\.\d+\.\d+\.\d+', text)))
    return {"output": {"enriched_ips": [{"ip": ip, "geo": None, "abuse_score": None} for ip in ips]}}
