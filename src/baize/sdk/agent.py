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

from baize.sdk.client import ChatMessage, CompletionResult, CompletionUsage, LLMClient, estimate_tokens


# 上下文预算内置默认值（模型配置未显式指定时使用）
# 现代商业模型窗口普遍 128K+（部分 256K/1M），默认预算取 256K，
# 可容纳大型分析内容（如单个流量包解析结果 20 万+ token）。
# 若配置了 ``context_window``，预算自动按 窗口 × _BUDGET_WINDOW_RATIO 推导。
DEFAULT_MAX_CONTEXT_TOKENS = 256000  # 上下文 token 预算上限
DEFAULT_MAX_MESSAGE_CHARS = 80000    # 单条消息最大字符数（超长自动截断）
_BUDGET_WINDOW_RATIO = 0.9           # 模型窗口 -> 预算 的比例（预留输出空间）

# 渐进式压缩（骨架阶段）目标长度：旧轮次先压缩为"骨架"而非直接删除
_COMPRESS_USER_CHARS = 12000   # 旧轮次 user 提问压缩目标字符数
_COMPRESS_REPLY_CHARS = 6000   # 旧轮次 assistant 回复 / 工具结果压缩目标字符数

# 语义摘要（可选）相关
_SUMMARY_PROMPT = (
    "请把下面一段对话压缩为简洁的中文摘要，尽量保留其中的关键事实、"
    "数据、路径、指标、判断与结论，不要遗漏重要信息，不要添加新内容：\n\n{content}"
)
_SUMMARY_MAX_ROUNDS = 5        # 单次裁剪中最多摘要的轮数（防止失控）
_SUMMARY_MIN_CHARS = 2000      # 轮次内容总字符数低于该值不值得摘要

# 工具循环内裁剪防抖：自上次裁剪后新增消息不足该条数不重复裁剪
_TRIM_DEBOUNCE_MSGS = 4


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
        except (KeyError, ValueError, IndexError):
            # 提示词中可能含有字面 { }（如 flag{...}、JSON/代码示例），
            # format 会抛 KeyError/ValueError/IndexError，此时原样返回。
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

    # ------------------------------------------------------------------
    # 上下文预算管理：token 裁剪 / 骨架压缩 / 语义摘要 / 防抖
    # ------------------------------------------------------------------

    def _context_budget(self) -> tuple[int, int, bool]:
        """读取模型配置，返回 (max_ctx_tokens, max_message_chars, enable_summary)。

        预算推导规则（优先级从高到低）：
        1. ``max_context_tokens`` 显式配置（0 表示不限制）。
        2. ``context_window`` 配置：预算 = 窗口 × 90%（预留输出空间）。
        3. 内置默认 256000。
        """
        from baize.sdk.client import get_active_model_config

        cfg = get_active_model_config()
        if cfg is None:
            return DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_MAX_MESSAGE_CHARS, False
        ctx = cfg.max_context_tokens
        if ctx is None:
            if cfg.context_window:
                ctx = max(1, int(cfg.context_window * _BUDGET_WINDOW_RATIO))
            else:
                ctx = DEFAULT_MAX_CONTEXT_TOKENS
        msg = cfg.max_message_chars if cfg.max_message_chars is not None else DEFAULT_MAX_MESSAGE_CHARS
        return ctx, msg, bool(cfg.enable_context_summary)

    @staticmethod
    def _truncate_message_content(text: str, max_chars: int) -> str:
        """截断单条消息：保留头尾各一半，中间标注原文长度。

        结果总长严格不超过 ``max_chars``（标注长度从配额中扣除）。
        """
        if not text or len(text) <= max_chars:
            return text
        marker = f"...[内容过长已截断，原文 {len(text)} 字符]..."
        if len(marker) >= max_chars:
            # 极端情况：上限过小放不下标注，直接硬截
            return text[:max_chars]
        half = (max_chars - len(marker)) // 2
        return f"{text[:half]}{marker}{text[-half:]}"

    def _truncate_long_messages(
        self, history: list[ChatMessage], max_message_chars: int
    ) -> None:
        """阶段 0：单条超长消息（如工具输出）截断保留头尾。"""
        if max_message_chars <= 0:
            return
        for m in history:
            if m.content_parts:
                for part in m.content_parts:
                    if part.get("type") == "text" and part.get("text"):
                        part["text"] = self._truncate_message_content(
                            str(part["text"]), max_message_chars
                        )
            elif m.content and len(m.content) > max_message_chars:
                m.content = self._truncate_message_content(m.content, max_message_chars)

    def _estimate_history_tokens(
        self,
        history: list[ChatMessage],
        tool_schemas: Optional[list[dict]] = None,
    ) -> int:
        """估算整段历史的 token 数（含消息结构开销与工具 schema）。"""
        total = 0
        for m in history:
            total += 4  # 每条消息的结构开销
            if m.content:
                total += estimate_tokens(m.content)
            if m.content_parts:
                for part in m.content_parts:
                    total += estimate_tokens(str(part.get("text", "")))
                total += 256  # 多模态内容块结构开销
            if m.tool_calls:
                total += 10 * len(m.tool_calls)
                for tc in m.tool_calls:
                    fn = tc.get("function", {})
                    total += estimate_tokens(str(fn.get("name", "")))
                    total += estimate_tokens(str(fn.get("arguments", "")))
            if m.name:
                total += 8
        if tool_schemas:
            total += estimate_tokens(json.dumps(tool_schemas, ensure_ascii=False))
        return total

    @staticmethod
    def _compress_old_turns(history: list[ChatMessage]) -> None:
        """骨架压缩：把"最新一轮 user 提问之前"的旧消息压缩为较短"骨架"。

        - 旧 user 提问：压缩到 ``_COMPRESS_USER_CHARS`` 字符
        - 旧 assistant 回复 / 工具结果：压缩到 ``_COMPRESS_REPLY_CHARS`` 字符
        - 均保留头尾并标注原文长度，尽量保留对话语义。

        system 指令与最新一轮（user 提问及之后的工具调用链）不压缩，
        保证正在进行的对话协议完整。
        """
        last_user = max(i for i, m in enumerate(history) if m.role == "user")
        for i in range(1, last_user):
            m = history[i]
            if m.role == "user":
                limit = _COMPRESS_USER_CHARS
            elif m.role in ("assistant", "tool"):
                limit = _COMPRESS_REPLY_CHARS
            else:
                continue
            if m.content and len(m.content) > limit:
                m.content = Agent._truncate_message_content(m.content, limit)
            if m.content_parts:
                for part in m.content_parts:
                    if part.get("type") == "text" and part.get("text") and len(str(part["text"])) > limit:
                        part["text"] = Agent._truncate_message_content(str(part["text"]), limit)

    async def _summarize_old_turns(
        self,
        history: list[ChatMessage],
        tool_schemas: Optional[list[dict]],
        max_ctx_tokens: int,
        client: Optional[LLMClient] = None,
    ) -> bool:
        """语义摘要（可选）：用 LLM 把最旧的真实对话轮压缩为一条摘要消息。

        从最旧一轮开始逐轮消化（每轮最多 ``_SUMMARY_MAX_ROUNDS`` 轮），
        每轮结束后重新估算预算。轮次内容太短（< ``_SUMMARY_MIN_CHARS``）
        或摘要调用失败时停止，交给机械压缩/删除兜底。

        始终保留最新一轮 user 提问及其后续工具链（协议完整性），
        与骨架压缩/删除逻辑保持一致。

        返回是否发生了摘要。
        """
        if max_ctx_tokens <= 0:
            return False
        llm = client or LLMClient()
        rounds_done = 0
        summarized = False
        while (
            rounds_done < _SUMMARY_MAX_ROUNDS
            and self._estimate_history_tokens(history, tool_schemas) > max_ctx_tokens
        ):
            # 找最旧的真实对话轮（跳过 system 与已有的摘要消息，
            # 且不得晚于最新一轮 user 提问）
            last_user = max(i for i, m in enumerate(history) if m.role == "user")
            start = None
            for i, m in enumerate(history):
                if i >= last_user:
                    break
                if m.role == "user" and not (m.content or "").startswith("[上下文摘要]"):
                    start = i
                    break
            if start is None or start == 0:
                break
            end = start + 1
            while end < len(history) and history[end].role != "user":
                end += 1
            total_chars = sum(len(m.content or "") for m in history[start:end])
            if total_chars < _SUMMARY_MIN_CHARS:
                break
            excerpt = []
            for m in history[start:end]:
                excerpt.append(f"[{m.role}] {(m.content or '')[:2000]}")
            try:
                result = await llm.complete(
                    [ChatMessage(role="user", content=_SUMMARY_PROMPT.format(content="\n".join(excerpt)))]
                )
                summary = (result.content or "").strip()
            except Exception:
                break  # 摘要失败：放弃摘要，交给机械压缩/删除
            if not summary:
                break
            history[start:end] = [
                ChatMessage(role="assistant", content=f"[上下文摘要] {summary}")
            ]
            summarized = True
            rounds_done += 1
        return summarized

    def _trim_history_to_budget(
        self,
        history: list[ChatMessage],
        max_ctx_tokens: int,
        tool_schemas: Optional[list[dict]] = None,
    ) -> list[ChatMessage]:
        """同步兜底裁剪：骨架压缩 -> 最旧轮整组删除，直到满足预算。

        - 始终保留 system 指令与最新一轮 user 提问（协议完整性）。
        - ``max_ctx_tokens <= 0`` 表示不限制。
        """
        if max_ctx_tokens <= 0:
            return history

        # 骨架压缩（比直接删除更保留信息）
        self._compress_old_turns(history)
        if self._estimate_history_tokens(history, tool_schemas) <= max_ctx_tokens:
            return history

        # 从最旧的一轮开始整组删除，直到满足预算
        while (
            len(history) > 2
            and self._estimate_history_tokens(history, tool_schemas) > max_ctx_tokens
        ):
            last_user = max(i for i, m in enumerate(history) if m.role == "user")
            removed = False
            for i in range(1, len(history)):
                if history[i].role == "user" and i != last_user:
                    end = i + 1
                    while end < len(history) and history[end].role != "user":
                        end += 1
                    del history[i:end]
                    removed = True
                    break
            if not removed:
                # 仅剩最新一轮仍超预算：不再删除（保留协议完整），交由后续请求处理
                break
        return history

    async def _trim_history_async(
        self,
        history: list[ChatMessage],
        tool_schemas: Optional[list[dict]],
        client: Optional[LLMClient] = None,
    ) -> list[ChatMessage]:
        """上下文裁剪总入口（异步，含可选语义摘要）。

        处理顺序：阶段 0 单条截断 -> 语义摘要（若开启）-> 骨架压缩 -> 最旧轮删除。
        """
        max_ctx_tokens, max_message_chars, enable_summary = self._context_budget()
        self._truncate_long_messages(history, max_message_chars)
        if max_ctx_tokens <= 0:
            return history
        if (
            enable_summary
            and self._estimate_history_tokens(history, tool_schemas) > max_ctx_tokens
        ):
            await self._summarize_old_turns(history, tool_schemas, max_ctx_tokens, client)
        return self._trim_history_to_budget(history, max_ctx_tokens, tool_schemas)

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
        3. 每次工具结果追加后按需裁剪上下文（防抖：自上次裁剪后新增
           消息不足 ``_TRIM_DEBOUNCE_MSGS`` 条不重复裁剪，避免抖动）。
        4. 继续请求，直到模型停止调用工具（finish_reason=stop）。
        """
        tool_by_name = {t.name: t for t in self.tools}
        total = CompletionUsage()
        last_trim_msgs = len(history)
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
                # 防抖裁剪
                if len(history) - last_trim_msgs >= _TRIM_DEBOUNCE_MSGS:
                    await self._trim_history_async(history, tool_schemas, client)
                    last_trim_msgs = len(history)
                continue  # 继续请求模型，获取工具执行后的最终回复

            # 无工具调用：返回模型文本
            return result.content, total

        return "", total

    async def run(
        self,
        user_message: str,
        context_variables: Optional[dict] = None,
        experience_block: Optional[str] = None,
    ) -> RunResult:
        """执行一次完整对话。

        experience_block: 可选的"历史经验"文本块，插入到 system 指令之后，
            供模型参考以往渗透测试的复盘经验（仅供参考，不影响系统指令优先级）。
        """
        client = LLMClient()
        history = self._build_history(user_message, context_variables)
        if experience_block:
            history.insert(1, ChatMessage(role="system", content=experience_block))
        tool_schemas = [t.to_schema() for t in self.tools]
        # 请求前按预算裁剪（含可选语义摘要）
        await self._trim_history_async(history, tool_schemas, client)
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
        experience_block: Optional[str] = None,
    ) -> AsyncIterator[AgentEvent]:
        """流式执行对话，逐步产出事件（支持实时思考、工具调用与上下文延续）。

        实现方式：使用真正的流式 ``client.stream()``，在流式过程中：
        - 将模型的 ``reasoning_content`` 实时产出为 ``reasoning`` 事件；
        - 累积流式工具调用增量，执行工具后继续请求，直到模型停止调用。

        extra_tools: 附加的会话级工具（如附件读取工具），会合并进模型工具集。
        user_chat_message: 若提供，作为当前用户消息（支持多模态图片内容块），
            否则用 user_message 字符串构造。
        experience_block: 可选的"历史经验"文本块，插入到 system 指令之后，
            供模型参考以往渗透测试的复盘经验。
        """
        client = LLMClient()
        if user_chat_message is not None:
            history = self._build_history(user_message, context_variables, prior_history)
            # 用多模态 user 消息替换末尾的纯文本 user 消息
            history = history[:-1] + [user_chat_message]
        else:
            history = self._build_history(user_message, context_variables, prior_history)
        if experience_block:
            history.insert(1, ChatMessage(role="system", content=experience_block))
        # 合并附加工具
        tools = list(self.tools)
        if extra_tools:
            tools.extend(extra_tools)
        tool_schemas = [t.to_schema() for t in tools]
        tool_by_name = {t.name: t for t in tools}
        # 请求前按预算裁剪（含可选语义摘要）
        await self._trim_history_async(history, tool_schemas, client)
        last_trim_msgs = len(history)
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
                # 防抖裁剪（自上次裁剪后新增消息不足阈值不重复裁剪）
                if len(history) - last_trim_msgs >= _TRIM_DEBOUNCE_MSGS:
                    await self._trim_history_async(history, tool_schemas, client)
                    last_trim_msgs = len(history)
                continue  # 继续请求模型，获取工具执行后的最终回复

            # 无工具调用：最终文本即为累积的 content
            final_text = "".join(text_parts)
            if final_text:
                yield AgentEvent(type="text", content=final_text)
            break

        yield AgentEvent(type="done", content=final_text)
