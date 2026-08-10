"""Baize Agent 运行框架（独立实现）。

提供工具调用（tool-calling）式的智能体运行循环，支持：
- 自定义系统指令（instructions）
- 工具注册与调用
- 普通与流式对话
- 工具调用事件流

本模块为 Baize 独立编写，不依赖任何第三方 agent 框架。
"""

from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional, Protocol

from baize.sdk.client import ChatMessage, CompletionResult, CompletionUsage, LLMClient


class Tool(Protocol):
    """工具协议。"""

    name: str
    description: str

    async def run(self, **kwargs: Any) -> str: ...


@dataclass
class AgentTool:
    """可调用的工具封装。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            args = {}
        result = self.handler(**args)
        if asyncio.iscoroutine(result):
            result = await result
        return str(result)


@dataclass
class RunResult:
    """一次运行的结果。"""

    final_output: Any = None
    messages: list[ChatMessage] = field(default_factory=list)
    usage: CompletionUsage = field(default_factory=CompletionUsage)


@dataclass
class AgentEvent:
    """流式运行事件。"""

    type: str  # "reasoning" | "text" | "tool_call" | "tool_result" | "done"
    content: str = ""
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[str] = None


@dataclass
class Agent:
    """智能体定义与运行器。

    Attributes
    ----------
    name: 智能体名称。
    description: 智能体功能描述（供前端展示）。
    instructions: 系统指令（可含 ``{context_variables}`` 占位符）。
    model: 模型名称（默认取全局单模型配置）。
    tools: 可用工具列表。
    max_tool_calls: 单轮对话内最大工具调用次数（防止失控）。
    """

    name: str
    description: str = ""
    instructions: str = ""
    model: Optional[str] = None
    tools: list[AgentTool] = field(default_factory=list)
    max_tool_calls: int = 30

    def _render_instructions(self, context_variables: Optional[dict] = None) -> str:
        ctx = context_variables or {}
        try:
            return self.instructions.format(**ctx)
        except (KeyError, ValueError):
            return self.instructions

    def _apply_context_window(
        self,
        history: list[ChatMessage],
        max_turns: int,
        current_user_message: str,
    ) -> list[ChatMessage]:
        """对历史应用滑动窗口裁剪，返回精简后的完整消息列表。

        保留规则：
        - 第一条 system 指令始终保留。
        - 最多保留 ``max_turns`` 轮 user/assistant 问答（最近的一轮）。
        - 工具调用产生的 assistant/tool 消息与对应的 user 提问绑定为"一轮"，
          裁剪时不会把一轮拆散（保留整组）。
        - ``max_turns <= 0`` 表示不限制、保留全部。
        - 当前即将发送的 user 消息始终保留在末尾。
        """
        if max_turns <= 0:
            return history + [ChatMessage(role="user", content=current_user_message)]

        # 去除首条 system，其余作为普通对话消息
        body = history[1:]

        # 将对话按 user 提问切分为若干"轮"（boundaries 记录每轮 user 消息的下标）
        boundaries: list[int] = []
        for i, m in enumerate(body):
            if m.role == "user":
                boundaries.append(i)

        if not boundaries:
            # 无历史提问：直接追加当前消息
            return history + [ChatMessage(role="user", content=current_user_message)]

        # 每轮的起点：该轮 user 下标，终点为下一轮 user 下标 - 1
        # 保留最近 max_turns 轮（从最后一个 boundary 往前数）
        keep_start_idx = boundaries[max(0, len(boundaries) - max_turns)]
        truncated = body[keep_start_idx:]

        return history[:1] + truncated + [ChatMessage(role="user", content=current_user_message)]

    def _build_history(
        self,
        user_message: str,
        context_variables: Optional[dict] = None,
        prior_history: Optional[list[ChatMessage]] = None,
    ) -> list[ChatMessage]:
        system = self._render_instructions(context_variables)
        history: list[ChatMessage] = [ChatMessage(role="system", content=system)]
        # 加载先前的对话上下文（会话持久化的历史消息）
        if prior_history:
            history.extend(prior_history)

        # 应用可配置的上下文滑动窗口（0 表示不限制）
        from baize.sdk.client import get_active_model_config

        max_turns = 0
        cfg = get_active_model_config()
        if cfg is not None:
            max_turns = int(cfg.context_max_turns or 0)
        return self._apply_context_window(history, max_turns, user_message)

    async def _run_tool_loop(
        self,
        client: LLMClient,
        history: list[ChatMessage],
        tool_schemas: Optional[list[dict]],
    ) -> tuple[str, CompletionUsage]:
        """执行工具调用循环，返回 (最终文本, 累计用量)。

        完整实现 OpenAI 工具调用协议：
        1. 请求模型；若返回 tool_calls，执行对应工具。
        2. 将 assistant 消息（含 tool_calls）和 tool 结果消息追加回历史。
        3. 继续请求，直到模型停止调用工具（finish_reason=stop）。
        """
        tool_by_name = {t.name: t for t in self.tools}
        total = CompletionUsage()
        for _ in range(self.max_tool_calls):
            result = await client.complete(history, tools=tool_schemas)
            total.input_tokens += result.usage.input_tokens
            total.output_tokens += result.usage.output_tokens
            total.reasoning_tokens += result.usage.reasoning_tokens

            if result.tool_calls:
                # 模型请求调用工具
                assistant_msg = ChatMessage(
                    role="assistant",
                    content=result.content,
                    tool_calls=result.tool_calls,
                )
                history.append(assistant_msg)
                # 执行每个工具，把结果作为 tool 消息追加
                for tc in result.tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    arguments = fn.get("arguments", "{}")
                    tool = tool_by_name.get(name)
                    if tool is None:
                        output = f"(工具 {name} 不存在)"
                    else:
                        output = await tool.execute(arguments)
                    history.append(
                        ChatMessage(
                            role="tool",
                            content=output,
                            tool_call_id=tc.get("id", ""),
                            name=name,
                        )
                    )
                continue  # 继续请求模型，获取工具执行后的最终回复

            # 无工具调用：返回模型文本
            return result.content, total

        return "", total

    async def run(
        self,
        user_message: str,
        context_variables: Optional[dict] = None,
    ) -> RunResult:
        """执行一次完整对话。"""
        client = LLMClient()
        history = self._build_history(user_message, context_variables)
        tool_schemas = [t.to_schema() for t in self.tools]
        content, usage = await self._run_tool_loop(client, history, tool_schemas)
        return RunResult(
            final_output=content,
            messages=history,
            usage=usage,
        )

    async def run_stream(
        self,
        user_message: str,
        context_variables: Optional[dict] = None,
        prior_history: Optional[list[ChatMessage]] = None,
        extra_tools: Optional[list[AgentTool]] = None,
        user_chat_message: Optional[ChatMessage] = None,
    ) -> AsyncIterator[AgentEvent]:
        """流式执行对话，逐步产出事件（支持实时思考、工具调用与上下文延续）。

        实现方式：使用真正的流式 ``client.stream()``，在流式过程中：
        - 将模型的 ``reasoning_content`` 实时产出为 ``reasoning`` 事件；
        - 累积流式工具调用增量，执行工具后继续请求，直到模型停止调用。

        extra_tools: 附加的会话级工具（如附件读取工具），会合并进模型工具集。
        user_chat_message: 若提供，作为当前用户消息（支持多模态图片内容块），
            否则用 user_message 字符串构造。
        """
        client = LLMClient()
        if user_chat_message is not None:
            history = self._build_history(user_message, context_variables, prior_history)
            # 用多模态 user 消息替换末尾的纯文本 user 消息
            history = history[:-1] + [user_chat_message]
        else:
            history = self._build_history(user_message, context_variables, prior_history)
        # 合并附加工具
        tools = list(self.tools)
        if extra_tools:
            tools.extend(extra_tools)
        tool_schemas = [t.to_schema() for t in tools]
        tool_by_name = {t.name: t for t in tools}
        final_text = ""

        for _ in range(self.max_tool_calls):
            tool_calls: list[dict] = []
            tool_accum: dict[int, dict] = {}  # index -> 累积的工具调用片段
            text_parts: list[str] = []

            # 流式请求模型
            async for result in client.stream(history, tools=tool_schemas):
                if result.reasoning:
                    # 实时思考过程
                    yield AgentEvent(type="reasoning", content=result.reasoning)
                elif result.content:
                    text_parts.append(result.content)
                    # 暂时不实时产出 text，等确认是否有工具调用后再决定
                elif result.tool_calls_delta:
                    # 累积流式工具调用增量（按 index 对齐 id/name/arguments 分片）
                    for tc in result.tool_calls_delta:
                        idx = tc.index
                        if idx not in tool_accum:
                            tool_accum[idx] = {
                                "id": getattr(tc, "id", None) or "",
                                "name": "",
                                "arguments": "",
                            }
                        if getattr(tc, "id", None):
                            tool_accum[idx]["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if fn.name:
                                tool_accum[idx]["name"] = fn.name
                            if fn.arguments:
                                tool_accum[idx]["arguments"] += fn.arguments

            # 组装完整工具调用（若有）
            if tool_accum:
                tool_calls = [
                    {
                        "id": acc["id"],
                        "type": "function",
                        "function": {
                            "name": acc["name"],
                            "arguments": acc["arguments"] or "{}",
                        },
                    }
                    for acc in tool_accum.values()
                ]

            if tool_calls:
                # 模型请求调用工具
                history.append(
                    ChatMessage(
                        role="assistant",
                        content="".join(text_parts),
                        tool_calls=tool_calls,
                    )
                )
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    tool = tool_by_name.get(name)
                    output = (
                        f"(工具 {name} 不存在)" if tool is None
                        else await tool.execute(fn.get("arguments", "{}"))
                    )
                    yield AgentEvent(type="tool_call", tool_name=name, tool_args=fn.get("arguments", "{}"))
                    yield AgentEvent(type="tool_result", tool_name=name, tool_result=output)
                    history.append(
                        ChatMessage(role="tool", content=output, tool_call_id=tc.get("id", ""), name=name)
                    )
                continue  # 继续请求模型，获取工具执行后的最终回复

            # 无工具调用：最终文本即为累积的 content
            final_text = "".join(text_parts)
            if final_text:
                yield AgentEvent(type="text", content=final_text)
            break

        yield AgentEvent(type="done", content=final_text)
