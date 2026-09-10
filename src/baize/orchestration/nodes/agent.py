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

            # 3b. 内置智能体已废弃 — 找不到时用 DynamicAgentFactory 临时创建
            if agent is None and node.agent:
                from baize.pentest.dynamic_agent import AgentSpec, get_factory
                factory = get_factory()
                role = node.agent.replace("_", " ").title()
                spec = AgentSpec(
                    role_prompt=f"你是{role}。基于流水线任务指令完成分析，输出客观发现。",
                    tools=[],
                    reasoning=f"流水线节点 {node.id} 指派 agent={node.agent}",
                    confidence=0.7,
                )
                agent = factory.create_agent(spec, session_id=state.get("session_id", ""))

            if agent is None:
                raise RuntimeError(f"Agent '{node.agent}' 未注册且无法动态创建")

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
