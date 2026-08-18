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
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional, Protocol

from baize.sdk.client import ChatMessage, CompletionResult, CompletionUsage, LLMClient, estimate_tokens
from baize.sdk.memory import BaseMemory
from baize.sdk.session_log import SessionLog

logger = logging.getLogger("baize.agent")


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

# 工具循环"阶段性结论"提示：连续纯工具调用轮数达到该值后，向历史追加收敛提示，
# 要求模型先给出阶段性结论，防止陷入无限工具探索导致 30 轮空转、无文本输出。
_CONCLUDE_HINT_TOOL_TURNS = 5


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
        # 区分同步/异步 handler：
        # - 异步 handler（如编排智能体的 run_specialist 等）：直接在事件循环中
        #   await 执行（其内部应为纯异步实现，如 async LLM 调用，不会阻塞事件循环）。
        # - 同步 handler（如内部使用 subprocess.run 的 shell/代码执行工具）放到
        #   线程池执行：避免阻塞事件循环，保证 SSE 心跳与其它并发请求不被卡死。
        # 注意：不能对异步 handler 使用 asyncio.to_thread——它只会在线程池中创建
        # 协程对象（函数体不执行），随后仍在事件循环中运行，防阻塞机制形同虚设。
        handler = self.handler
        if inspect.iscoroutinefunction(handler):
            result = await handler(**args)
        else:
            result = await asyncio.to_thread(handler, **args)
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
    tool_call_id: Optional[str] = None  # 工具调用 ID：用于会话持久化时配对 call/output


@dataclass
class Agent:
    """智能体定义与运行器。

    Attributes
    ----------
    name: 智能体名称。
    description: 智能体功能描述（供前端展示）。
    instructions: 系统指令（可含 ``{context_variables}`` 占位符）。
    model: 模型名称（默认取全局单模型配置）。
    model_provider: 模型提供方名（模型注册表中的名称，如
        "openai-compatible"），为 None 时使用全局单模型配置。
    model_router: 可选的 ModelRouter 实例；提供后优先使用路由/fallback。
    tools: 可用工具列表。
    max_tool_calls: 单轮对话内最大工具调用次数（防止失控）。
    state: 运行时状态字典，随每次运行合并进 context_variables 注入系统
        指令（占位符 ``{state}``），工具可读写该字典共享中间结果。
    memory: 可选记忆实现（BaseMemory）。运行前加载记忆文本注入系统
        指令，运行结束后保存对话历史。
    hooks: 回调钩子字典，键为钩子名，值为 callable 或 callable 列表
        （同步/异步皆可；多个处理器按序执行，实现事件链叠加）：
        - "on_start":  (agent, user_message, context_variables)
        - "on_tool_call": 工具调用前的事件链（瀑布式）。旧式签名
          (agent, tool_name, arguments) 自动继续；瀑布式签名
          (agent, tool_name, arguments, next) 可调用 next() 继续、
          next(new_arguments) 改写参数、或返回 {"deny": True, "reason": ...}
          拦截本次调用 —— 多个策略插件可叠加执行。
        - "on_tool_result": (agent, tool_name, result)
        - "on_text":   (agent, text)
        - "on_done":   (agent, final_output)
        - "on_error":  (agent, error)
    session_id: 会话标识，用于记忆存取（缺省用全局默认会话）。
    session_log: 可选的 append-only 会话日志（SessionLog）。配置后每次
        运行自动记录 user/message、agent/request、agent/response、
        tool/call、tool/result、turn 边界与会话生命周期事件，支持审计重放。
    """

    name: str
    description: str = ""
    instructions: str = ""
    model: Optional[str] = None
    model_provider: Optional[str] = None
    model_router: Optional[Any] = None
    tools: list[AgentTool] = field(default_factory=list)
    max_tool_calls: int = 30
    state: dict = field(default_factory=dict)
    memory: Optional[BaseMemory] = None
    hooks: dict[str, Callable] = field(default_factory=dict)
    session_id: Optional[str] = None
    session_log: Optional[SessionLog] = None

    # ------------------------------------------------------------------
    # 钩子触发与记忆辅助
    # ------------------------------------------------------------------

    def _hook_handlers(self, hook_name: str) -> list[Callable]:
        """返回指定钩子的处理器列表（兼容单 callable 与 list）。"""
        hook = self.hooks.get(hook_name)
        if hook is None:
            return []
        if isinstance(hook, (list, tuple)):
            return list(hook)
        return [hook]

    async def _emit(self, hook_name: str, *args: Any) -> None:
        """触发指定钩子（支持单 handler / 多 handler 列表，同步/异步皆可）。

        逐个调用所有处理器，异常仅告警不中断。
        """
        for handler in self._hook_handlers(hook_name):
            try:
                result = handler(*args)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.warning("钩子 %s 执行失败: %s", hook_name, exc)

    @staticmethod
    def _hook_accepts_next(handler: Callable) -> bool:
        """判断处理器是否为瀑布式签名（声明了 ``next`` 参数）。

        瀑布式处理器签名: ``(agent, tool_name, arguments, next)``。
        旧式处理器签名: ``(agent, tool_name, arguments)`` —— 自动继续链。
        """
        try:
            sig = inspect.signature(handler)
            return "next" in sig.parameters
        except (TypeError, ValueError):  # 内建对象等无签名
            return False

    async def _tool_call_chain(
        self, tool_name: str, arguments: str
    ) -> tuple[bool, str, Optional[str]]:
        """on_tool_call 瀑布式事件链：多个策略插件可叠加执行。

        每个处理器可:
        - 调用 ``next()`` 继续链（修改参数: ``next(new_arguments)``）。
        - 返回 ``{"deny": True, "reason": "..."}`` 拦截本次工具调用（短路）。
        - 旧式签名 ``(agent, name, arguments)`` 无 next 参数时自动继续。

        Returns:
            (是否放行, 最终参数, 拒绝原因)
        """
        handlers = self._hook_handlers("on_tool_call")
        if not handlers:
            return True, arguments, None
        current_args = arguments
        index = 0

        async def run_next(new_args: Optional[str] = None) -> Optional[dict]:
            nonlocal current_args, index
            if new_args is not None:
                current_args = new_args
            if index >= len(handlers):
                return None
            handler = handlers[index]
            index += 1
            try:
                if self._hook_accepts_next(handler):
                    result = handler(self, tool_name, current_args, run_next)
                else:
                    result = handler(self, tool_name, current_args)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:  # noqa: BLE001
                logger.warning("on_tool_call 钩子执行失败: %s", exc)
                return await run_next()
            if result is None:
                return await run_next()
            if isinstance(result, dict) and result.get("deny"):
                return result  # 策略拦截，短路
            if isinstance(result, dict) and "arguments" in result:
                return await run_next(result["arguments"])  # 改写参数继续
            return await run_next()

        decision = await run_next()
        if isinstance(decision, dict) and decision.get("deny"):
            return False, current_args, decision.get("reason", "未说明")
        return True, current_args, None

    def _log_event(self, kind: str, **payload: Any) -> None:
        """追加一条会话日志事件（未配置 session_log 时静默跳过）。"""
        if self.session_log is None:
            return
        try:
            self.session_log.append(kind, **payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("会话日志写入失败 (%s): %s", kind, exc)

    def _ensure_session_started(self) -> None:
        """确保会话日志已记录 session/start（首条事件，可重复调用）。"""
        if self.session_log is not None and len(self.session_log) == 0:
            self._log_event("session/start", session_id=self.session_log.session_id)

    def _memory_block(self) -> Optional[str]:
        """加载记忆文本块（空串/无记忆返回 None）。"""
        if self.memory is None:
            return None
        try:
            text = self.memory.load(self.session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆加载失败: %s", exc)
            return None
        return text.strip() or None

    def _save_memory(self, history: list[ChatMessage], final_output: Any = None) -> None:
        """运行结束后保存记忆（异常仅告警）。

        final_output: 最终回复文本。``_run_tool_loop`` 直接返回文本而未
            追加到 history，因此单独传入，保证记忆能记录最终结论。
        """
        if self.memory is None:
            return
        try:
            msgs = list(history)
            if final_output is not None:
                msgs.append(ChatMessage(role="assistant", content=str(final_output)))
            self.memory.save(msgs, self.session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆保存失败: %s", exc)

    def _resolve_client(self) -> Any:
        """解析当前 Agent 使用的模型客户端。

        优先级:
        1. 显式传入的 ``model_router``（支持 fallback 链）。
        2. ``model_provider`` 指定的注册表提供方。
        3. 全局 ModelRouter（primary=openai-compatible）。
        4. 回退到旧 ``LLMClient``（单模型配置，完全向后兼容）。

        Returns:
            具有 ``complete`` / ``stream`` 方法的模型客户端实例。
        """
        if self.model_router is not None:
            return self.model_router.model(self.model_provider or self.model)
        if self.model_provider:
            from baize.sdk.models import model_registry

            try:
                return model_registry.create(self.model_provider)
            except KeyError:
                pass  # 未注册则回退默认
        # 默认：全局 ModelRouter（含 fallback），行为等价 LLMClient
        from baize.sdk.models import ModelRouter

        try:
            router = ModelRouter()
            return router.model(self.model)
        except Exception:  # noqa: BLE001
            # 完全回退：旧 LLMClient（单模型）
            from baize.sdk.client import LLMClient

            return LLMClient()

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
        # 注入记忆块（位于系统指令之后，供模型参考历史结论）
        mem_block = self._memory_block()
        if mem_block:
            history.append(
                ChatMessage(role="system", content=f"[历史记忆]\n{mem_block}")
            )
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

    def _merged_context(self, context_variables: Optional[dict] = None) -> dict:
        """合并调用方 context_variables 与 Agent 运行时 state（state 优先注入）。"""
        ctx = dict(context_variables or {})
        if self.state:
            ctx.setdefault("state", self.state)
        return ctx

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
        _, max_message_chars, _ = self._context_budget()
        self._ensure_session_started()
        turn_index = 0
        for _ in range(self.max_tool_calls):
            turn_index += 1
            self._log_event("turn/start", index=turn_index)
            self._log_event(
                "agent/request",
                model=getattr(client, "model", None) or type(client).__name__,
                message_count=len(history),
            )
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
                self._log_event("agent/response", content=result.content or "", tool_calls=result.tool_calls)
                # 执行每个工具，把结果作为 tool 消息追加
                for tc in result.tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    arguments = fn.get("arguments", "{}")
                    tool = tool_by_name.get(name)
                    self._log_event("tool/call", name=name, arguments=arguments)
                    if tool is None:
                        output = f"(工具 {name} 不存在)"
                        self._log_event("tool/result", name=name, output=output, denied=False, reason="工具未注册")
                    else:
                        # 瀑布式策略链：审计/权限/危险命令拦截可叠加，可短路
                        allowed, final_args, deny_reason = await self._tool_call_chain(name, arguments)
                        if not allowed:
                            output = f"(工具调用已被策略拦截: {deny_reason or '未说明'})"
                            self._log_event("tool/result", name=name, output=output, denied=True, reason=deny_reason)
                        else:
                            started_at = asyncio.get_event_loop().time()
                            output = await tool.execute(final_args)
                            duration = asyncio.get_event_loop().time() - started_at
                            self._log_event("tool/result", name=name, output=output, denied=False, duration=round(duration, 4))
                        await self._emit("on_tool_result", self, name, output)
                    history.append(
                        ChatMessage(
                            role="tool",
                            content=output,
                            tool_call_id=tc.get("id", ""),
                            name=name,
                        )
                    )
                self._log_event("turn/end", index=turn_index)
                # 单条超长消息（如超大工具输出）无条件截断，防止超出模型输入长度上限；
                # 昂贵的语义摘要 / 整轮删除仍由下方防抖逻辑控制。
                self._truncate_long_messages(history, max_message_chars)
                # 防抖裁剪
                if len(history) - last_trim_msgs >= _TRIM_DEBOUNCE_MSGS:
                    await self._trim_history_async(history, tool_schemas, client)
                    last_trim_msgs = len(history)
                continue  # 继续请求模型，获取工具执行后的最终回复

            # 无工具调用：返回模型文本
            self._log_event("agent/response", content=result.content or "")
            self._log_event("turn/end", index=turn_index)
            return result.content, total

        self._log_event("turn/end", index=turn_index)
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
        ctx = self._merged_context(context_variables)
        await self._emit("on_start", self, user_message, ctx)
        self._ensure_session_started()
        self._log_event("user/message", content=user_message)
        try:
            client = self._resolve_client()
            history = self._build_history(user_message, ctx)
            if experience_block:
                history.insert(1, ChatMessage(role="system", content=experience_block))
            tool_schemas = [t.to_schema() for t in self.tools]
            # 请求前按预算裁剪（含可选语义摘要）
            await self._trim_history_async(history, tool_schemas, client)
            content, usage = await self._run_tool_loop(client, history, tool_schemas)
            await self._emit("on_done", self, content)
            self._log_event("session/end", reason="done")
            self._save_memory(history, content)
            return RunResult(
                final_output=content,
                messages=history,
                usage=usage,
            )
        except Exception as exc:  # noqa: BLE001
            await self._emit("on_error", self, exc)
            self._log_event("session/end", reason="error", error=str(exc))
            raise

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
        ctx = self._merged_context(context_variables)
        await self._emit("on_start", self, user_message, ctx)
        self._ensure_session_started()
        self._log_event("user/message", content=user_message)
        try:
            client = self._resolve_client()
            if user_chat_message is not None:
                history = self._build_history(user_message, ctx, prior_history)
                # 用多模态 user 消息替换末尾的纯文本 user 消息
                history = history[:-1] + [user_chat_message]
            else:
                history = self._build_history(user_message, ctx, prior_history)
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
            _, max_message_chars, _ = self._context_budget()
            final_text = ""
            tool_call_seq: dict = {}  # 工具调用 ID 序号（模型未给 id 时兜底生成）
            tool_calls_executed = 0  # 本轮已执行的工具调用数（用于空回复兜底判断）
            forced_conclusion = False  # 是否已强制要求模型继续任务（避免无限追加）
            tool_turns_since_conclusion = 0  # 连续纯工具调用轮数（用于阶段性结论提示）
            conclusion_hint_added = False     # 是否已追加阶段性结论提示（避免重复）
            turn_index = 0

            for _ in range(self.max_tool_calls):
                turn_index += 1
                self._log_event("turn/start", index=turn_index)
                self._log_event(
                    "agent/request",
                    model=getattr(client, "model", None) or type(client).__name__,
                    message_count=len(history),
                )
                tool_calls: list[dict] = []
                tool_accum: dict[int, dict] = {}  # index -> 累积的工具调用片段
                text_parts: list[str] = []

                # 流式请求模型
                async for result in client.stream(history, tools=tool_schemas):
                    if result.reasoning:
                        # 实时思考过程
                        yield AgentEvent(type="reasoning", content=result.reasoning)
                    # 注意：content / reasoning / tool_calls 可能来自同一 chunk 的多个事件，
                    # 必须独立判断，避免工具调用增量被 content 分支吞掉。
                    if result.content:
                        text_parts.append(result.content)
                        # 暂时不实时产出 text，等确认是否有工具调用后再决定
                    if result.tool_calls_delta:
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
                    tool_calls_executed += len(tool_calls)
                    tool_turns_since_conclusion += 1
                    history.append(
                        ChatMessage(
                            role="assistant",
                            content="".join(text_parts),
                            tool_calls=tool_calls,
                        )
                    )
                    self._log_event("agent/response", content="".join(text_parts), tool_calls=tool_calls)
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        name = fn.get("name", "")
                        # 生成稳定的工具调用 ID（模型未返回 id 时用序号兜底），
                        # 供会话持久化配对 function_call / function_call_output，
                        # 这样"继续"时历史可以无损重建，避免任务从头重跑。
                        if not tool_call_seq:
                            tool_call_seq = {}
                        tc_id = tc.get("id") or f"call_{len(tool_call_seq)}"
                        tool_call_seq.setdefault(tc_id, True)
                        tool = tool_by_name.get(name)
                        arguments = fn.get("arguments", "{}")
                        self._log_event("tool/call", name=name, arguments=arguments, call_id=tc_id)
                        yield AgentEvent(
                            type="tool_call",
                            tool_name=name,
                            tool_args=arguments,
                            tool_call_id=tc_id,
                        )
                        # 瀑布式策略链：多个策略插件可叠加，可短路拦截
                        allowed, final_args, deny_reason = await self._tool_call_chain(name, arguments)
                        if not allowed:
                            output = f"(工具调用已被策略拦截: {deny_reason or '未说明'})"
                            self._log_event("tool/result", name=name, output=output, denied=True, reason=deny_reason)
                        elif tool is None:
                            output = f"(工具 {name} 不存在)"
                            self._log_event("tool/result", name=name, output=output, denied=False, reason="工具未注册")
                        else:
                            started_at = asyncio.get_event_loop().time()
                            output = await tool.execute(final_args)
                            duration = asyncio.get_event_loop().time() - started_at
                            self._log_event("tool/result", name=name, output=output, denied=False, duration=round(duration, 4))
                        await self._emit("on_tool_result", self, name, output)
                        yield AgentEvent(type="tool_result", tool_name=name, tool_result=output, tool_call_id=tc_id)
                        history.append(
                            ChatMessage(role="tool", content=output, tool_call_id=tc_id, name=name)
                        )
                    self._log_event("turn/end", index=turn_index)
                    # 单条超长消息（如超大工具输出）无条件截断，防止超出模型输入长度上限；
                    # 昂贵的语义摘要 / 整轮删除仍由下方防抖逻辑控制。
                    self._truncate_long_messages(history, max_message_chars)
                    # 防抖裁剪（自上次裁剪后新增消息不足阈值不重复裁剪）
                    if len(history) - last_trim_msgs >= _TRIM_DEBOUNCE_MSGS:
                        await self._trim_history_async(history, tool_schemas, client)
                        last_trim_msgs = len(history)
                    # 连续多轮纯工具调用后，追加"阶段性结论"提示，防止模型空转
                    # 耗尽 max_tool_calls 而最终无文本输出（仅提示一次，避免刷屏）。
                    if (
                        tool_turns_since_conclusion >= _CONCLUDE_HINT_TOOL_TURNS
                        and not conclusion_hint_added
                    ):
                        conclusion_hint_added = True
                        history.append(
                            ChatMessage(
                                role="user",
                                content="（注意：已连续多轮调用工具。请基于已获得的工具结果给出阶段性结论与当前发现，并明确下一步的关键假设；不要继续无方向地重复探索或执行相似操作。）",
                            )
                        )
                    continue  # 继续请求模型，获取工具执行后的最终回复

                # 无工具调用：最终文本即为累积的 content
                final_text = "".join(text_parts)
                self._log_event("agent/response", content=final_text)
                self._log_event("turn/end", index=turn_index)
                if final_text:
                    tool_turns_since_conclusion = 0
                    conclusion_hint_added = False
                    yield AgentEvent(type="text", content=final_text)
                    await self._emit("on_text", self, final_text)
                    break

                # 兜底：模型没有产出任何文本（工具调用后返回空回复，或直接空回复），
                # 主动要求其**继续完成任务**，而不是静默结束或只给半截结论。
                # 最多兜底一次，避免无限循环。
                if not forced_conclusion:
                    forced_conclusion = True
                    if tool_calls_executed > 0:
                        history.append(
                            ChatMessage(
                                role="user",
                                content="（注意：上一轮工具调用已经执行完成。请基于已获得的工具结果继续完成任务，给出明确结论与下一步动作；不要重复执行已经做过的步骤，也不要留空回复。）",
                            )
                        )
                    else:
                        history.append(
                            ChatMessage(role="user", content="请继续回答我的问题，不要留空回复。")
                        )
                    continue

                break

            yield AgentEvent(type="done", content=final_text)
            await self._emit("on_done", self, final_text)
            self._log_event("session/end", reason="done")
            self._save_memory(history, final_text)
        except Exception as exc:  # noqa: BLE001
            await self._emit("on_error", self, exc)
            self._log_event("session/end", reason="error", error=str(exc))
            raise
