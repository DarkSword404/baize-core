"""
DataTransformer 节点执行器 — 将接收器原始数据转换为流水线可用格式。
支持 Jinja2 模板和内置转换函数。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from jinja2 import Template

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)

# ---- 内置转换器 ----

def _html_to_text(html: str) -> str:
    """HTML → 纯文本（去除标签）"""
    return re.sub(r'<[^>]+>', ' ', html).strip()


def _extract_json_from_text(text: str) -> dict[str, Any]:
    """从文本中提取 JSON 块"""
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"raw": text}


def _syslog_parse(line: str) -> dict[str, Any]:
    """粗略解析 Syslog 行"""
    result: dict[str, Any] = {"raw": line}
    # RFC 3164: <PRI>TIMESTAMP HOSTNAME MSG
    m = re.match(r'<(\d+)>(\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+(.*)', line)
    if m:
        result["priority"] = int(m.group(1))
        result["timestamp"] = m.group(2)
        result["hostname"] = m.group(3)
        result["message"] = m.group(4)
    return result


_BUILTIN_TRANSFORMERS = {
    "html_to_text": _html_to_text,
    "extract_json": _extract_json_from_text,
    "syslog_parse": _syslog_parse,
    "identity": lambda x: x,
    "passthrough": lambda x: x,
}


class DataTransformerNodeExecutor(BaseNodeExecutor):
    """数据转换器 — 基于模板或内置函数。"""

    node_type = "datatransformer"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        try:
            # 获取上游 receiver 的输出
            steps = state.get("steps", {})
            # 找最近的 receiver 节点输出
            upstream_output = ""
            for key, val in reversed(steps.items()):
                if isinstance(val, dict) and val.get("type") == "receiver":
                    upstream_output = val.get("output", "")
                    break
            if not upstream_output:
                # 降级：使用最近任意节点的输出
                for key, val in reversed(steps.items()):
                    if isinstance(val, dict):
                        upstream_output = val.get("output", "")
                        break

            transformer_name = node.agent or "identity"

            # 查找内置转换器
            if transformer_name in _BUILTIN_TRANSFORMERS:
                fn = _BUILTIN_TRANSFORMERS[transformer_name]
                result_data = fn(upstream_output)
            elif node.prompt_template:
                # 使用 Jinja2 模板
                tmpl = Template(node.prompt_template)
                rendered = tmpl.render(input=upstream_output, steps=steps)
                # 尝试解析为 JSON
                try:
                    result_data = json.loads(rendered)
                except json.JSONDecodeError:
                    result_data = {"output": rendered}
            else:
                # 直通
                result_data = {"output": upstream_output}

            output_str = json.dumps(result_data, ensure_ascii=False, default=str)

            updates.update(self._record_done(node, state, output_str, result_data))
            updates["route"] = ""

        except Exception as e:
            logger.exception(f"DataTransformer 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates
