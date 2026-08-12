"""
流水线执行引擎 — 调用 Baize Agent 完成单个步骤，支持 LangGraph 节点集成。
"""

from __future__ import annotations

import asyncio
import json
import datetime
from typing import Any, AsyncIterator

from baize.agents import get_agent
from baize.orchestration.graph import PipelineGraphCompiler, resolve_template
from baize.orchestration.state import PipelineState


class PipelineEngine:
    """流水线执行引擎。

    管理流水线的编译与执行。每个步骤通过调用现有 Baize Agent 完成。
    """

    def __init__(self, pipeline_def: dict):
        self._definition = pipeline_def
        self._compiler = PipelineGraphCompiler(self._make_node)

    def compile(self):
        """编译为 LangGraph compiled graph。"""
        return self._compiler.compile(self._definition)

    def _make_node(self, step: dict):
        """生产 LangGraph 节点函数。

        每个节点包装一个 Baize Agent 调用（或人工确认逻辑）。
        """
        step_id = step.get("id", "")
        step_type = step.get("type", "agent")
        agent_name = step.get("agent", "")
        prompt_template = step.get("prompt", "")
        tools = step.get("tools", [])

        async def agent_node(state: PipelineState) -> PipelineState:
            """Agent 步骤节点：调用 Baize Agent 执行任务。"""
            state["current_step"] = step_id

            # 初始化步骤状态
            state["steps"][step_id] = {
                "step_id": step_id,
                "agent_name": agent_name or "unknown",
                "status": "running",
                "result": "",
                "data": {},
                "error": "",
            }

            try:
                # 解析模板变量
                prompt = resolve_template(prompt_template, state)

                # 获取 Agent
                agent = get_agent(agent_name)
                if agent is None:
                    agent = get_agent(None)

                # 组装历史上下文
                prior_messages = state.get("messages", [])
                from baize.sdk.client import ChatMessage
                prior_history = [
                    ChatMessage(role=m.get("role", "user"), content=m.get("content", ""))
                    for m in prior_messages[-10:]  # 保留最近 10 条
                ]

                # 执行 Agent（非流式）
                result_text = ""
                async for event in agent.run_stream(
                    prompt,
                    prior_history=prior_history,
                ):
                    if event.type == "text":
                        result_text += event.content
                    elif event.type == "done":
                        result_text = event.content or result_text

                # 尝试从输出中提取 JSON
                parsed_data = _try_parse_json(result_text)

                state["steps"][step_id] = {
                    "step_id": step_id,
                    "agent_name": agent_name or agent.default_name,
                    "status": "completed",
                    "result": result_text,
                    "data": parsed_data.get("data", parsed_data) if isinstance(parsed_data, dict) else {},
                    "error": "",
                }

                # 追加到消息历史
                state["messages"].append({"role": "assistant", "content": result_text})

            except Exception as e:
                state["steps"][step_id] = {
                    "step_id": step_id,
                    "agent_name": agent_name or "unknown",
                    "status": "failed",
                    "result": "",
                    "data": {},
                    "error": str(e),
                }
                state["error"] = str(e)
                state["status"] = "failed"

            return state

        async def condition_node(state: PipelineState) -> PipelineState:
            """条件分支节点：根据 rules 决定下一步路由。"""
            state["current_step"] = step_id
            state["steps"][step_id] = {
                "step_id": step_id,
                "agent_name": "condition",
                "status": "completed",
                "result": "条件判断完成",
                "data": {},
                "error": "",
            }
            return state

        async def human_node(state: PipelineState) -> PipelineState:
            """人工确认节点：暂停执行等待用户响应。"""
            confirm_prompt = resolve_template(step.get("prompt", ""), state)

            state["current_step"] = step_id
            state["steps"][step_id] = {
                "step_id": step_id,
                "agent_name": "human_confirm",
                "status": "interrupted",
                "result": confirm_prompt,
                "data": {
                    "choices": step.get("choices", ["approve", "reject"]),
                    "branches": step.get("branches", {}),
                },
                "error": "",
            }
            state["confirm_required"] = True
            state["confirm_prompt"] = confirm_prompt
            state["confirm_step_id"] = step_id

            return state

        async def end_node(state: PipelineState) -> PipelineState:
            """结束节点：标记流水线完成。"""
            state["status"] = "completed"
            # 生成总结报告
            report_lines = []
            for sid, sstate in state.get("steps", {}).items():
                status = sstate.get("status", "")
                result = sstate.get("result", "")
                if result:
                    report_lines.append(f"## {sstate.get('agent_name', sid)} ({status})")
                    summary = result[:500] + ("..." if len(result) > 500 else "")
                    report_lines.append(summary)
                    report_lines.append("")
            state["report"] = "\n".join(report_lines)
            return state

        if step_type == "condition":
            return condition_node
        if step_type == "human":
            return human_node
        if step_type == "end":
            return end_node
        return agent_node


def _try_parse_json(text: str) -> dict:
    """尝试从文本中提取 JSON 块。"""
    # 先找 ```json ... ``` 代码块
    import re
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # 尝试整段 JSON
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    return {}
