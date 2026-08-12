"""
Agent 节点执行器 — 调用 LLM Agent 执行安全分析任务。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


class AgentNodeExecutor(BaseNodeExecutor):
    """调用 LLM Agent 执行安全任务。"""

    node_type = "agent"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        # 1. 标记开始
        updates: dict[str, Any] = self._record_start(node, state)

        try:
            # 2. 渲染提示词
            prompt = self._render_template(node.prompt_template, state)

            # 3. 调用 Agent SDK
            from baize.sdk import get_agent
            agent = get_agent(node.agent)
            if agent is None:
                raise RuntimeError(f"Agent '{node.agent}' 未注册")

            # 调用 Agent.run(user_message) 执行对话
            result = await agent.run(user_message=prompt)
            output = result.final_output or ""
            data: dict[str, Any] = {}

            # 尝试解析 JSON 输出
            try:
                # 提取可能的 JSON 块
                import re
                json_match = re.search(r'\{[\s\S]*\}', output)
                if json_match:
                    data = json.loads(json_match.group())
            except (json.JSONDecodeError, AttributeError):
                data = {"raw_output": output}

            # 4. 标记完成
            updates.update(self._record_done(node, state, output, data))
            updates["messages"] = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": output},
            ]

            # 5. 设置路由 — agent 完成后默认去下一个节点
            updates["route"] = ""

        except Exception as e:
            logger.exception(f"Agent 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates
