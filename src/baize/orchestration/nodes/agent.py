"""
Agent 节点执行器 — 调用 LLM Agent 执行安全分析任务。
"""

from __future__ import annotations

import json
import logging
import time
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

            # 3. 调用 Agent SDK（get_agent 定义于 baize.agents，支持别名解析）
            from baize.agents import get_agent
            agent = get_agent(node.agent)
            if agent is None and node.agent:
                # 模板引用别名失败时降级为模糊匹配（兼容历史模板名）
                from baize.agents import list_agents
                _name = node.agent.lower()
                for info in list_agents():
                    if _name in info["name"].lower() or _name in info.get("id", "").lower():
                        agent = get_agent(info["id"])
                        break
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
            # 4b. 追加到流水线对话（与核心会话库隔离，随 run 保存/回收）
            updates["dialog"] = [{
                "kind": "llm",
                "node": node.id,
                "node_type": node.type,
                "agent": node.agent or "",
                "prompt": prompt,
                "output": output,
                "timestamp": time.time(),
            }]

            # 5. 设置路由 — agent 完成后默认去下一个节点
            updates["route"] = ""

        except Exception as e:
            logger.exception(f"Agent 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates
