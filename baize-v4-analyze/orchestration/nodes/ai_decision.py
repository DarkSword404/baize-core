"""
AI 决策节点执行器 — AI-SOAR 核心。

`decision` 是规则版（表达式求值），`ai_decision` 是 LLM 版：
综合上下文、上游工具输出和分支描述，由 LLM 选择唯一分支。

执行语义：
- 渲染 `decision_prompt`（Jinja2），携带 context / steps / state
- 调用 LLMClient 让模型输出 `{"branch", "reasoning", "confidence"}`
- `branch` 写入 PipelineState["route"]，编译器据此走条件边
- `reasoning` / `confidence` / `model_output` 写入节点 record 的 data（审计日志）
- LLM 不可用 / 输出非法时优雅回退默认分支，并在 data 中标记 fallback
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import BranchRule, PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 输出中提取 JSON 对象（容忍 markdown 代码块 / 前缀文本）。"""
    if not text:
        return {}
    text = text.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退而求其次：截取第一个 { ... } 块
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


class AIDecisionNodeExecutor(BaseNodeExecutor):
    """AI 决策节点：LLM 根据上下文路由到目标分支。"""

    node_type = "ai_decision"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        default_target = self._default_target(node)
        branch = default_target
        reasoning = ""
        confidence = 0.0
        fallback = False
        error = ""
        raw = ""

        try:
            prompt = self._render_template(node.decision_prompt, state)
            branch, reasoning, confidence, raw = await self._decide(node, prompt)
        except Exception as e:  # 模型未配置 / 网络错误 / 超时等
            logger.exception("ai_decision 节点 '%s' LLM 调用失败，回退默认分支", node.id)
            fallback = True
            error = str(e)

        valid_targets = self._valid_targets(node)
        if branch not in valid_targets:
            # LLM 输出了非法分支：回退默认分支，保留原始输出供审计
            invalid = branch
            branch = default_target
            fallback = True
            error = error or f"LLM 返回非法分支 {invalid!r}，可选: {sorted(valid_targets)}"

        data: dict[str, Any] = {
            "branch": branch,
            "reasoning": reasoning,
            "confidence": round(confidence, 4),
            "fallback": fallback,
            "error": error,
            "model_output": raw,
        }
        output = f"AI 决策: {branch}" + (f"（回退，原因: {error}）" if fallback else "")
        updates.update(self._record_done(node, state, output, data))
        updates["route"] = branch
        return updates

    async def _decide(self, node: PipelineNode, prompt: str) -> tuple[str, str, float, str]:
        """调用 LLM 获取决策，返回 (branch, reasoning, confidence, raw_output)。"""
        from baize.sdk.client import LLMClient, ChatMessage

        options = self._options(node)
        option_lines = []
        for b in options:
            hint = f"（判断依据: {b.condition}）" if b.condition else ""
            flag = "（默认分支）" if b.is_default else ""
            option_lines.append(f"- {b.target!r}: {b.label or b.target}{hint}{flag}")
        system = (
            "你是安全编排流水线的 AI 决策引擎。根据给定上下文，选择唯一正确的路由分支。\n"
            "可用分支:\n"
            + ("\n".join(option_lines) if option_lines else "(无)\n")
            + "\n"
            "只输出一个 JSON 对象，不要输出任何其他内容（不要用 markdown 代码块包裹）:\n"
            '{"branch": "<分支 target>", "reasoning": "<100 字内的决策理由>", "confidence": <0.0~1.0>}'
        )
        client = LLMClient()  # 使用全局模型配置（~/.baize/model.json）
        result = await client.complete(
            [ChatMessage("system", system), ChatMessage("user", prompt)],
            temperature=0.2,
        )
        raw = result.content or ""
        data = _extract_json(raw)
        branch = str(data.get("branch", "")).strip()
        reasoning = str(data.get("reasoning", "")).strip()
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return branch, reasoning, confidence, raw

    # ---- 辅助 ----

    def _options(self, node: PipelineNode) -> list[BranchRule]:
        return node.branches or []

    def _valid_targets(self, node: PipelineNode) -> set[str]:
        return {b.target for b in node.branches}

    def _default_target(self, node: PipelineNode) -> str:
        for b in node.branches:
            if b.is_default:
                return b.target
        if node.branches:
            return node.branches[0].target
        return ""
