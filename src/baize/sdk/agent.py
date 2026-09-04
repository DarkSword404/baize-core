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
import os

import httpx
import openai
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional, Protocol

from baize.sdk.client import ChatMessage, CompletionResult, CompletionUsage, LLMClient, estimate_tokens
from baize.sdk.memory import BaseMemory
from baize.sdk.session_log import SessionLog, SessionEvent
from baize.compressor import CompressorConfig, ToolOutputCompressor
from baize.services import get as _svc_get
from baize.sandbox import PermissionLevel

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

# LLM 调用重试：临时性故障（网络抖动/超时/限流/5xx）做指数退避重试；
# 配置类错误（404/401/400 等）不重试，直接抛出交由上层诊断，
# 避免配置损坏时反复无效请求、拖垮整轮对话。
_LLM_RETRY_ATTEMPTS = 4        # 额外重试次数（总尝试 = 1 + 4 = 5 次，退避 1s→2s→4s→8s，覆盖约 15 秒抖动窗口）
_LLM_RETRY_BACKOFF_BASE = 1.0  # 指数退避基础秒数（1s -> 2s -> 4s -> 8s）

# 工具执行超时：单次工具调用（含同步 handler 的线程池执行）超过该秒数
# 即中止等待并返回超时错误文本给模型，防止工具挂起（网络卡住/subprocess
# 阻塞等）拖死整轮对话。超时后同步 handler 的底层线程仍会在后台跑完，
# 但对话流程可继续，不阻塞后续轮次。
#
# 注意这是一刀切的兜底上限：部分工具自身声明了更长的超时（如 hashcat_crack /
# john 的 600s），上限偏小会把它提前截断，表现为"这些工具永远超时失败"。
# 跑长任务（全端口扫描、口令破解）时调大该值，设为 <=0 表示不限制。
DEFAULT_TOOL_EXEC_TIMEOUT = 300.0  # 单位：秒


def _resolve_tool_exec_timeout() -> float:
    """解析工具执行超时上限（秒）。

    可用环境变量 ``BAIZE_TOOL_EXEC_TIMEOUT`` 覆盖；``<=0`` 表示不限制。
    每次调用都重新读取，便于运行期调整而不用重启服务。
    """
    raw = os.environ.get("BAIZE_TOOL_EXEC_TIMEOUT", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TOOL_EXEC_TIMEOUT
    return value if value > 0 else 0.0


def _missing_required_params(handler: Callable[..., Any], args: dict[str, Any]) -> list[str]:
    """返回 handler 中缺失的必填参数名列表（无法内省时返回空列表）。"""
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return []
    missing: list[str] = []
    for name, param in sig.parameters.items():
        if name in args:
            continue
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            if param.default is inspect.Parameter.empty:
                missing.append(name)
    return missing


def _handler_accepts_param(handler: Callable[..., Any], name: str) -> bool:
    """handler 是否显式声明了指定参数名（或通过 **kwargs 接受任意参数）。"""
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):  # 内建/不可内省对象
        return False
    if name in sig.parameters:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _tool_missing_hint(name: str, exc: Exception) -> str:
    """工具执行因系统二进制缺失失败时，附加可安装指引（便于模型/用户直接定位）。"""
    msg = str(exc)
    if any(k in msg for k in ("command not found", "not found", "No such file or directory")):
        return (
            f"(工具 {name} 执行失败: {type(exc).__name__}: {msg}。"
            f"可能原因: 该系统依赖的二进制未安装。请在项目目录运行 ./install-tools.sh 预装工具，"
            f"或手动安装对应命令后重试。)"
        )
    return f"(工具 {name} 执行失败: {type(exc).__name__}: {msg})"


def _httpx_transport_exc_types() -> tuple[type[Exception], ...]:
    """动态收集 httpx 与 httpx2（openai SDK 3.x 底层）两包的传输层异常。

    openai SDK 3.x 内部使用独立的 httpx2 包，其异常类与顶层 httpx 不互通，
    仅靠 ``isinstance(exc, httpx.xxx)`` 判定会漏掉 httpx2 抛出的同类异常
    （例如 ``httpx2.RemoteProtocolError``）。按名称动态导入，某包未安装时
    自动跳过对应类。
    """
    names = (
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ReadError",
        "RemoteProtocolError",
        "NetworkError",
        "CloseError",
    )
    exc_types: list[type[Exception]] = []
    for module_name in ("httpx", "httpx2"):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        for name in names:
            cls = getattr(module, name, None)
            if cls is not None:
                exc_types.append(cls)
    return tuple(exc_types)


def _is_retryable_llm_error(exc: Exception) -> bool:
    """判断 LLM 调用异常是否属于值得重试的临时性故障。

    可重试：httpx / httpx2 网络层异常、openai 超时/连接错误/限流/HTTP 5xx。
    不可重试：404/401/400/403 等配置类 4xx —— 重试无意义，
    直接抛出交由上层诊断（例如 base_url 失效导致的 NotFoundError）。
    """
    if isinstance(exc, _httpx_transport_exc_types()):
        return True
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)):
        return True
    if isinstance(exc, openai.InternalServerError):
        return True
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", None)
        return status is not None and status >= 500
    return False


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
        # 防御：LLM 可能生成非法参数形态（"null"、"[1,2]""、"字符串"等），
        # json.loads 不报错但结果非 dict，后续 `name in args` 会抛 TypeError。
        # 一律归一为 dict，保持调用链健壮。
        if not isinstance(args, dict):
            args = {}
        # 防御：handler 必填参数缺失时返回友好错误而非抛 TypeError，
        # 避免 LLM 省略参数导致整个对话流 500 中断。
        missing = _missing_required_params(self.handler, args)
        if missing:
            return (
                f"工具 `{self.name}` 调用缺少必填参数: "
                f"{', '.join(missing)}。请补全后重新调用。"
            )
        # 区分同步/异步 handler：
        # - 异步 handler（如编排智能体的 run_specialist 等）：直接在事件循环中
        #   await 执行（其内部应为纯异步实现，如 async LLM 调用，不会阻塞事件循环）。
        # - 同步 handler（如内部使用 subprocess.run 的 shell/代码执行工具）放到
        #   线程池执行：避免阻塞事件循环，保证 SSE 心跳与其它并发请求不被卡死。
        # 注意：不能对异步 handler 使用 asyncio.to_thread——它只会在线程池中创建
        # 协程对象（函数体不执行），随后仍在事件循环中运行，防阻塞机制形同虚设。
        handler = self.handler
        timeout = _resolve_tool_exec_timeout()
        # <=0 表示不限制，asyncio.wait_for 需传 None
        limit: Optional[float] = timeout if timeout > 0 else None
        # 尊重工具声明的单次执行时长：部分工具（hashcat/john/aircrack 等）
        # 在参数里声明了更长 timeout（如 600s）。若运维未显式配置全局上限
        # （即 BAIZE_TOOL_EXEC_TIMEOUT 未设置、仍为默认值），则跟随工具声明，
        # 避免被默认 300s 一刀切提前截断，表现为"长任务工具永远超时失败"。
        # 运维一旦显式设置该 env，则以全局为准（硬顶）。
        if limit is not None and "BAIZE_TOOL_EXEC_TIMEOUT" not in os.environ:
            raw_timeout = args.get("timeout") if isinstance(args, dict) else None
            if (
                isinstance(raw_timeout, (int, float))
                and raw_timeout > 0
                and _handler_accepts_param(handler, "timeout")
            ):
                limit = max(limit, float(raw_timeout))
        try:
            if inspect.iscoroutinefunction(handler):
                # 异步 handler：直接在事件循环中 await（其内部应为纯异步实现，
                # 如 async LLM 调用，不会阻塞事件循环）。
                result = await asyncio.wait_for(handler(**args), timeout=limit)
            else:
                # 同步 handler（如内部使用 subprocess.run 的 shell/代码执行工具）
                # 放到线程池执行：避免阻塞事件循环。wait_for 超时后协程被取消，
                # 底层线程仍会在后台跑完，但对话流程可继续，不阻塞后续轮次。
                result = await asyncio.wait_for(
                    asyncio.to_thread(handler, **args), timeout=limit
                )
        except asyncio.TimeoutError:
            return (
                f"工具 `{self.name}` 执行超时（超过 {int(limit or 0)}s），"
                f"结果未获取。请告知用户执行超时或改用其他方法。"
            )
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

    type: str  # "reasoning" | "text" | "tool_call" | "tool_result" | "sandbox_approval" | "stream_reset" | "done"
    content: str = ""
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[str] = None
    tool_call_id: Optional[str] = None  # 工具调用 ID：用于会话持久化时配对 call/output
    sandbox_reason: Optional[str] = None  # 沙箱拦截原因
    sandbox_session_id: Optional[str] = None  # 沙箱审批会话 ID


# ── Skill 按工具名按需注入 (P6') ──

def _inject_skills_for_turn(history: list[ChatMessage], tool_count: int) -> None:
    """本轮工具执行后，把命中的技能文本按需注入 history。

    遍历本轮新增的 tool 消息的工具名，用 ``baize.skills`` 注册表解析对应
    技能文件（如 ``shared_browser_*`` 工具族首次使用时注入协作浏览器手册）。
    技能内容平时不常驻 system prompt，首次用到对应工具时才加入，省 token。

    已注入过的技能（history 中已有该技能标题行）跳过，避免重复灌入。
    """
    if tool_count <= 0:
        return
    # 收集本轮调用的工具名（按 tool 消息从后向前取 tool_count 条）
    names: list[str] = []
    for m in reversed(history):
        if len(names) >= tool_count:
            break
        if m.role == "tool" and m.name:
            names.append(m.name)
    if not names:
        return
    existing_content = "".join(m.content or "" for m in history)
    for name in reversed(names):
        try:
            from baize.skills import resolve_skill_text

            text = resolve_skill_text(name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("技能解析失败 (%s): %s", name, exc)
            continue
        if not text:
            continue
        first_line = text.splitlines()[0] if text.splitlines() else ""
        if first_line and first_line in existing_content:
            continue  # 该技能已在上下文中，跳过
        # role=user + "[系统提示]" 前缀：与工具图片注入一致的注入模式，
        # 避免在 tool 消息后插入 system 消息被严格实现拒绝。
        history.append(
            ChatMessage(
                role="user",
                content=(
                    "[系统提示] 以下为按需注入的技能手册（非用户消息），"
                    "仅供你后续操作参考：\n\n"
                    f"[技能手册 · {name} 工具族]\n{text}"
                ),
            )
        )
        existing_content += first_line


def _inject_tool_images(
    history: list[ChatMessage],
    tool_count: int,
) -> bool:
    """扫描本轮新增的工具输出消息，提取图片并注入多模态用户消息。

    反向扫描 history 中最近 tool_count 条 tool 消息，收集其中引用的
    图片文件路径，去重后构造一条多模态用户消息追加到 history 末尾。

    返回 True 表示至少注入了一张图片。
    """
    from baize.multimodal import build_tool_image_message

    if tool_count <= 0:
        return False

    # 收集本轮所有 tool 消息的输出文本
    tool_outputs: list[str] = []
    for m in reversed(history):
        if m.role == "tool" and m.content:
            tool_outputs.append(m.content)
            if len(tool_outputs) >= tool_count:
                break

    if not tool_outputs:
        return False

    # 合并所有输出，提取图片并构造消息
    combined = "\n".join(reversed(tool_outputs))
    img_msg = build_tool_image_message(combined)
    if img_msg is None:
        return False

    history.append(img_msg)
    return True


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
    tool_output_compressor: Optional[ToolOutputCompressor] = None
    """工具输出语义压缩器（TokenJuice 风格）。None 表示不压缩。"""

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
    ) -> tuple[bool, str, Optional[str], Optional[str]]:
        """on_tool_call 瀑布式事件链：多个策略插件可叠加执行。

        每个处理器可:
        - 调用 ``next()`` 继续链（修改参数: ``next(new_arguments)``）。
        - 返回 ``{"deny": True, "reason": "..."}`` 拦截本次工具调用（短路）。
        - 旧式签名 ``(agent, name, arguments)`` 无 next 参数时自动继续。

        Returns:
            (是否放行, 最终参数, 拒绝原因, 审批会话ID)
            第4个值仅在沙箱要求审批时非空，调用方应暂停等待用户审批。
        """
        # 沙箱边界检查：内置策略，在钩子链之前执行，确保始终生效
        sandbox = _svc_get("sandbox")
        session_id = self.session_log.session_id if self.session_log else self.session_id
        if sandbox is not None and session_id:
            try:
                check = sandbox.check(tool_name, str(session_id))
                if not check.get("allowed"):
                    if check.get("level") == PermissionLevel.APPROVE:
                        # 需要审批：返回 session_id 让调用方暂停等待
                        return False, arguments, check.get("reason", "需要审批"), str(session_id)
                    return False, arguments, check.get("reason", "沙箱策略拦截"), None
            except Exception as exc:  # noqa: BLE001
                logger.warning("沙箱检查失败: %s", exc)

        handlers = self._hook_handlers("on_tool_call")
        if not handlers:
            return True, arguments, None, None
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
            return False, current_args, decision.get("reason", "未说明"), None
        return True, current_args, None, None

    def _log_event(self, kind: str, **payload: Any) -> None:
        """追加一条会话日志事件（未配置 session_log 时静默跳过）。"""
        if self.session_log is None:
            return
        try:
            event = self.session_log.append(kind, **payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("会话日志写入失败 (%s): %s", kind, exc)
            return
        # 并行写入结构化数据库（DbRecorder），异常不影响主流程
        db = _svc_get("db_recorder")
        if db is not None:
            try:
                sid = self.session_log.session_id
                if kind in ("session/start", "user/message"):
                    # 幂等激活：首轮创建，后续轮（同会话复用日志时
                    # 只有 user/message 而无 session/start）重新置为 active
                    db.start_session(sid, self.name)
                elif kind == "session/end":
                    # reason 语义规范化：done -> completed，其余(如 error) -> interrupted
                    reason = payload.get("reason", "done")
                    status = "completed" if reason == "done" else "interrupted"
                    db.end_session(sid, status)
                db.record_event(sid, event)
                # 工具执行统计：错误先以 tool/error 标记，随后的 tool/result
                # 统一计数一次；被拦截 / 未注册的调用不构成真实执行，不计。
                # 集合为运行时属性（Agent 可被 dataclasses.replace 克隆），惰性初始化。
                pending = getattr(self, "_pending_tool_errors", None)
                if pending is None:
                    pending = set()
                    self._pending_tool_errors = pending
                if kind == "tool/error" and payload.get("name"):
                    pending.add(payload["name"])
                elif kind == "tool/result" and payload.get("name"):
                    name = payload["name"]
                    if (
                        not payload.get("denied")
                        and payload.get("reason") != "工具未注册"
                    ):
                        is_error = name in pending
                        pending.discard(name)
                        db.record_tool_call(
                            sid,
                            name,
                            payload.get("duration", 0) or 0.0,
                            error=is_error,
                        )
                    else:
                        # 被拒 / 未注册：不计数，但清掉可能的错误标记
                        self._pending_tool_errors.discard(name)
            except Exception as exc:  # noqa: BLE001
                logger.debug("DbRecorder 写入失败: %s", exc)

    def _ensure_session_started(self) -> None:
        """确保会话日志已记录 session/start（首条事件，可重复调用）。"""
        if self.session_log is not None and len(self.session_log) == 0:
            self._log_event("session/start", session_id=self.session_log.session_id)

    def _reset_sandbox_turn(self) -> None:
        """重置沙箱的本轮危险工具计数（每次用户提问开始时调用）。

        使 ``max_dangerous_per_turn`` 按"轮"生效，而非按整个会话累计。
        """
        sandbox = _svc_get("sandbox")
        session_id = self.session_log.session_id if self.session_log else self.session_id
        if sandbox is not None and session_id:
            try:
                sandbox.reset_turn(str(session_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("沙箱单轮计数重置失败: %s", exc)

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

    async def _maybe_retry_llm(
        self, exc: Exception, attempt: int, *, stream: bool = False, produced: bool = False
    ) -> bool:
        """LLM 调用异常后判断是否退避重试。

        仅对临时性故障（网络抖动/超时/限流/5xx）重试，配置类错误
        （404/401/400/403 等）不重试。返回 True 表示已等待退避、应重试；
        返回 False 表示应直接抛出原异常。

        produced: 流式场景下断连时是否已产出过部分内容（用于诊断日志，
            区分「连接阶段失败」与「流中断(已产出内容)」）。
        """
        if not _is_retryable_llm_error(exc) or attempt > _LLM_RETRY_ATTEMPTS:
            return False
        delay = _LLM_RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
        self._log_event(
            "agent/retry",
            attempt=attempt,
            error=type(exc).__name__,
            stream=stream,
            produced=produced,
            retry_in=round(delay, 2),
        )
        logger.warning(
            "LLM 调用失败（%s%s%s），%.1fs 后重试（第 %d/%d 次）",
            type(exc).__name__,
            "，连接阶段" if stream else "",
            "，流中断(已产出内容)" if produced else "",
            delay,
            attempt,
            _LLM_RETRY_ATTEMPTS,
        )
        await asyncio.sleep(delay)
        return True

    async def _complete_with_retry(
        self,
        client: LLMClient,
        history: list[ChatMessage],
        tool_schemas: Optional[list[dict]],
    ) -> CompletionResult:
        """调用模型完成一次非流式补全，带异常捕获与指数退避重试。

        仅对临时性故障（网络抖动/超时/限流/5xx）重试最多
        ``_LLM_RETRY_ATTEMPTS`` 次；配置类错误（404/401/400/403 等）
        不重试，直接抛出，避免配置损坏时反复无效请求。
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                return await client.complete(history, tools=tool_schemas)
            except Exception as exc:  # noqa: BLE001
                if not await self._maybe_retry_llm(exc, attempt):
                    raise

    async def _stream_llm_with_retry(
        self,
        client: LLMClient,
        history: list[ChatMessage],
        tool_schemas: Optional[list[dict]],
    ) -> AsyncIterator[CompletionResult]:
        """流式请求模型，带异常捕获与退避重试。

        连接建立阶段（尚未产出任何数据块）失败：安全退避重试；
        流中已产出部分内容（reasoning/content/工具增量）后断连：先产出
        ``CompletionResult(reset=True)`` 标记，由调用方清空本回合已缓冲的
        半截内容，再退避重试重新生成，避免内容重复渲染。
        """
        attempt = 0
        while True:
            attempt += 1
            produced = False
            try:
                async for result in client.stream(history, tools=tool_schemas):
                    produced = True
                    yield result
                return
            except Exception as exc:  # noqa: BLE001
                model_name = getattr(client, "model", None) or type(client).__name__
                logger.warning(
                    "LLM 流式请求失败: type=%s phase=%s model=%s messages=%d attempt=%d/%d",
                    type(exc).__name__,
                    "stream(produced)" if produced else "connect",
                    model_name,
                    len(history),
                    attempt,
                    _LLM_RETRY_ATTEMPTS,
                )
                if produced:
                    # 已产出部分内容后断连：先发 reset 标记清空半截缓冲，再退避重试
                    if await self._maybe_retry_llm(exc, attempt, stream=True, produced=True):
                        yield CompletionResult(content="", reset=True)
                        continue
                    raise
                if not await self._maybe_retry_llm(exc, attempt, stream=True):
                    raise

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
        forced_conclusion = False  # 空回复兜底：最多强制续写一次
        for _ in range(self.max_tool_calls):
            turn_index += 1
            self._log_event("turn/start", index=turn_index)
            self._log_event(
                "agent/request",
                model=getattr(client, "model", None) or type(client).__name__,
                message_count=len(history),
            )
            result = await self._complete_with_retry(client, history, tool_schemas)
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
                        allowed, final_args, deny_reason, approval_sid = await self._tool_call_chain(name, arguments)
                        if not allowed:
                            if approval_sid is not None:
                                # 沙箱要求审批：等待用户决定
                                sandbox = _svc_get("sandbox")
                                if sandbox is not None:
                                    approved = await sandbox.request_approval(name, approval_sid)
                                    if approved:
                                        # 用户批准：执行工具
                                        started_at = asyncio.get_running_loop().time()
                                        try:
                                            output = await tool.execute(final_args)
                                        except Exception as exc:  # noqa: BLE001
                                            output = _tool_missing_hint(name, exc)
                                            self._log_event("tool/error", name=name, error=str(exc))
                                            logger.warning("工具 %s 执行失败: %s: %s", name, type(exc).__name__, exc)
                                        duration = asyncio.get_running_loop().time() - started_at
                                        self._log_event("tool/result", name=name, output=output, denied=False, duration=round(duration, 4))
                                        sandbox.record_execution(name, approval_sid, True)
                                    else:
                                        output = f"(工具调用已被用户拒绝: {deny_reason or '未说明'})"
                                        self._log_event("tool/result", name=name, output=output, denied=True, reason=deny_reason)
                                else:
                                    output = f"(工具调用需要审批但沙箱不可用: {deny_reason or '未说明'})"
                                    self._log_event("tool/result", name=name, output=output, denied=True, reason=deny_reason)
                            else:
                                output = f"(工具调用已被策略拦截: {deny_reason or '未说明'})"
                                self._log_event("tool/result", name=name, output=output, denied=True, reason=deny_reason)
                        else:
                            started_at = asyncio.get_running_loop().time()
                            try:
                                output = await tool.execute(final_args)
                            except Exception as exc:  # noqa: BLE001
                                # 工具异常隔离：工具自身抛出的异常不中断整轮对话，
                                # 转为 tool 结果消息返回给模型，由模型决定重试或改道。
                                output = _tool_missing_hint(name, exc)
                                self._log_event("tool/error", name=name, error=str(exc))
                                logger.warning("工具 %s 执行失败: %s: %s", name, type(exc).__name__, exc)
                            duration = asyncio.get_running_loop().time() - started_at
                            self._log_event("tool/result", name=name, output=output, denied=False, duration=round(duration, 4))
                        await self._emit("on_tool_result", self, name, output)
                    # ── TokenJuice 语义压缩（hooks 拿到原始输出，LLM 拿到压缩版）──
                    if self.tool_output_compressor and self.tool_output_compressor.should_compress(name, output):
                        output = await self.tool_output_compressor.compress(name, output)
                    history.append(
                        ChatMessage(
                            role="tool",
                            content=output,
                            tool_call_id=tc.get("id", ""),
                            name=name,
                        )
                    )
                # P6': 技能按工具名按需注入（本轮调用过某工具族则加载其手册）
                try:
                    _inject_skills_for_turn(history, len(result.tool_calls))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("技能注入失败: %s", exc)
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
            # 空回复兜底：模型未调用工具且输出为空（幻觉/被裁剪等）时，
            # 最多强制续写一次，避免用户拿到空回复；再次为空则原样返回。
            if not result.content and not forced_conclusion:
                forced_conclusion = True
                history.append(
                    ChatMessage(role="user", content="请继续回答我的问题，不要留空回复。")
                )
                continue
            self._log_event("turn/end", index=turn_index)
            return result.content, total

        self._log_event("turn/end", index=turn_index)
        return "", total

    async def _try_auto_refine(self, client, user_message: str, final_text: str) -> None:
        """记忆自动学习：每回合结束，把本轮轨迹固化为 Episode 并提炼经验。

        依赖全局注册的 memory_service（全新记忆子系统 baize.memory）：
        - 工具调用 / 观察 / 结论按时间序固化为 Episode（原始证据，只增不改）；
        - 信号判定后提炼为自然语言经验（高置信度自动 active，否则 draft）。
        """
        svc = _svc_get("memory_service")
        if svc is None:
            return
        session_id = (self.session_log.session_id if self.session_log
                      else self.session_id or "")
        events = self._normalize_log_events(self._collect_tool_events())
        task = (user_message or "").strip()[:400]
        try:
            await svc.learn_events(
                events=events,
                task=task,
                agent_key=self.name,
                session_id=session_id,
                result_text=final_text or "",
                error_text="",
                status="failed" if not (final_text or "").strip() else "success",
                user_message=user_message,
                client=client,
            )
            logger.info(
                "记忆已学习: session=%s task=%s events=%d",
                session_id or "-", task[:60] or "(空)", len(events),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆学习异常: %s", exc)

    def _normalize_log_events(self, events: list[dict]) -> list[dict]:
        """把 SessionLog 的 tool/call + tool/result 事件配对成 Episode 工具调用。"""
        merged: dict[str, dict] = {}
        order: list[str] = []
        for ev in events:
            kind = ev.get("kind")
            if kind == "tool/call":
                key = str(ev.get("call_id") or f"call:{len(order)}")
                merged[key] = {
                    "type": "tool_call",
                    "name": ev.get("name", ""),
                    "arguments": ev.get("arguments", ""),
                    "output": "",
                    "status": "",
                    "ts": ev.get("ts", ev.get("timestamp", "")),
                }
                order.append(key)
            elif kind == "tool/result":
                key = ev.get("call_id")
                if key is not None and str(key) in merged:
                    key = str(key)
                else:
                    key = None
                    for cand in reversed(order):
                        rec = merged.get(cand, {})
                        if rec.get("name") == ev.get("name", "") and not rec.get("output"):
                            key = cand
                            break
                    if key is None:
                        key = f"call:{len(order)}"
                        merged[key] = {"type": "tool_call",
                                       "name": ev.get("name", ""),
                                       "arguments": "", "output": "",
                                       "status": "", "ts": ""}
                        order.append(key)
                merged[key]["output"] = ev.get("output", "")
                merged[key]["status"] = ("error" if ev.get("error")
                                         else ("denied" if ev.get("denied")
                                               else "ok"))
        return [merged[k] for k in order]

    def _collect_tool_events(self) -> list[dict]:
        """从会话日志中收集本轮工具调用事件。"""
        events: list[dict] = []
        if self.session_log is None:
            return events
        for ev in self.session_log:
            if ev.kind in ("tool/call", "tool/result"):
                events.append({"kind": ev.kind, **ev.payload})
        return events

    async def _compress_recent_tool_outputs(
        self,
        history: list[ChatMessage],
        tool_count: int,
    ) -> None:
        """对本轮新增的工具输出做语义压缩（只压缩送入 LLM 的历史）。

        反向扫描最近 ``tool_count`` 条 tool 消息并逐条压缩。前端 SSE 事件、
        会话日志与图片注入都已在压缩前拿到原始输出，因此本压缩只减少
        后续每轮重复发送给模型的 token，不影响用户可见内容。

        压缩失败或压缩无收益时保留原始输出，不阻塞对话。
        """
        comp = self.tool_output_compressor
        if comp is None or tool_count <= 0:
            return
        remaining = tool_count
        total_before = 0
        total_after = 0
        for m in reversed(history):
            if remaining <= 0:
                break
            if m.role != "tool":
                continue
            remaining -= 1
            text = m.content or ""
            if not text:
                continue
            name = m.name or ""
            if not comp.should_compress(name, text):
                continue
            try:
                new_text = await comp.compress(name, text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("工具输出压缩异常 (%s): %s，保留原始输出", name, exc)
                continue
            if new_text and new_text != text:
                total_before += len(text)
                total_after += len(new_text)
                m.content = new_text
        if total_before:
            logger.info(
                "工具输出压缩: %d→%d 字符 (%.0f%%)",
                total_before, total_after,
                total_after / max(total_before, 1) * 100,
            )

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
        # 新一轮开始：重置沙箱的单轮危险工具配额
        self._reset_sandbox_turn()
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

            # ── 经验自动提炼 (P1) ──
            await self._try_auto_refine(client, user_message, content)

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
        # 新一轮开始：重置沙箱的单轮危险工具配额
        self._reset_sandbox_turn()
        self._ensure_session_started()
        self._log_event("user/message", content=user_message)
        try:
            client = self._resolve_client()
            if user_chat_message is not None:
                history = self._build_history(user_message, ctx, prior_history)
                # 用多模态 user 消息替换末尾的纯文本 user 消息
                history = history[:-1] + [user_chat_message]
                logger.info(
                    "多模态 user_chat_message 已注入: content_parts=%s",
                    getattr(user_chat_message, "content_parts", None) is not None,
                )
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
            # 验证裁剪后最后一条 user 消息的 content_parts 是否保留
            last_user_msg = history[-1] if history else None
            if last_user_msg and last_user_msg.role == "user":
                logger.info(
                    "裁剪后末条 user 消息: has_content_parts=%s, content_len=%d",
                    last_user_msg.content_parts is not None,
                    len(last_user_msg.content or ""),
                )
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

                # 流式请求模型（连接阶段失败自动退避重试，中途断流直接抛出）
                async for result in self._stream_llm_with_retry(client, history, tool_schemas):
                    if result.reset:
                        # 断流恢复：清空本回合已缓冲的半截内容，通知前端从干净状态重新渲染
                        text_parts.clear()
                        tool_accum.clear()
                        tool_calls = []
                        yield AgentEvent(type="stream_reset")
                        continue
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
                        allowed, final_args, deny_reason, approval_sid = await self._tool_call_chain(name, arguments)
                        if not allowed:
                            if approval_sid is not None:
                                # 沙箱要求审批：通知前端并等待用户决定
                                yield AgentEvent(
                                    type="sandbox_approval",
                                    tool_name=name,
                                    tool_args=arguments,
                                    sandbox_reason=deny_reason,
                                    sandbox_session_id=approval_sid,
                                )
                                sandbox = _svc_get("sandbox")
                                if sandbox is not None:
                                    approved = await sandbox.request_approval(name, approval_sid)
                                    if approved:
                                        # 用户批准：执行工具
                                        started_at = asyncio.get_running_loop().time()
                                        try:
                                            output = await tool.execute(final_args)
                                        except Exception as exc:  # noqa: BLE001
                                            output = _tool_missing_hint(name, exc)
                                            self._log_event("tool/error", name=name, error=str(exc))
                                            logger.warning("工具 %s 执行失败: %s: %s", name, type(exc).__name__, exc)
                                        duration = asyncio.get_running_loop().time() - started_at
                                        self._log_event("tool/result", name=name, output=output, denied=False, duration=round(duration, 4))
                                        sandbox.record_execution(name, approval_sid, True)
                                    else:
                                        output = f"(工具调用已被用户拒绝: {deny_reason or '未说明'})"
                                        self._log_event("tool/result", name=name, output=output, denied=True, reason=deny_reason)
                                else:
                                    output = f"(工具调用需要审批但沙箱不可用: {deny_reason or '未说明'})"
                                    self._log_event("tool/result", name=name, output=output, denied=True, reason=deny_reason)
                            else:
                                output = f"(工具调用已被策略拦截: {deny_reason or '未说明'})"
                                self._log_event("tool/result", name=name, output=output, denied=True, reason=deny_reason)
                        elif tool is None:
                            output = f"(工具 {name} 不存在)"
                            self._log_event("tool/result", name=name, output=output, denied=False, reason="工具未注册")
                        else:
                            started_at = asyncio.get_running_loop().time()
                            try:
                                output = await tool.execute(final_args)
                            except Exception as exc:  # noqa: BLE001
                                # 工具异常隔离：工具自身抛出的异常不中断整轮对话，
                                # 转为 tool 结果消息返回给模型，由模型决定重试或改道。
                                output = _tool_missing_hint(name, exc)
                                self._log_event("tool/error", name=name, error=str(exc))
                                logger.warning("工具 %s 执行失败: %s: %s", name, type(exc).__name__, exc)
                            duration = asyncio.get_running_loop().time() - started_at
                            self._log_event("tool/result", name=name, output=output, denied=False, duration=round(duration, 4))
                        await self._emit("on_tool_result", self, name, output)
                        yield AgentEvent(type="tool_result", tool_name=name, tool_result=output, tool_call_id=tc_id)
                        history.append(
                            ChatMessage(role="tool", content=output, tool_call_id=tc_id, name=name)
                        )
                    # P6': 技能按工具名按需注入（本轮调用过某工具族则加载其手册）
                    try:
                        _inject_skills_for_turn(history, len(tool_calls))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("技能注入失败: %s", exc)
                    self._log_event("turn/end", index=turn_index)
                    # ── 工具输出图片自动注入 ──
                    # 扫描本轮所有工具输出，若发现图片文件路径，自动注入
                    # 多模态用户消息，让 LLM 能"看到"工具下载/生成的图片。
                    _injected = _inject_tool_images(history, len(tool_calls))
                    if _injected:
                        logger.info(
                            "工具图片已注入: turn=%d, tool_count=%d",
                            turn_index, len(tool_calls),
                        )
                    # ── TokenJuice 语义压缩 ──
                    # 放在图片注入之后：注入需要原始输出中的图片路径，先压缩会把它压掉。
                    # 前端 SSE 事件与会话日志早已拿到原始输出，这里只压缩送入 LLM 的副本。
                    await self._compress_recent_tool_outputs(history, len(tool_calls))
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
            # ── 经验自动提炼 (P1) ──
            await self._try_auto_refine(client, user_message, final_text)
            self._log_event("session/end", reason="done")
            self._save_memory(history, final_text)
        except Exception as exc:  # noqa: BLE001
            await self._emit("on_error", self, exc)
            self._log_event("session/end", reason="error", error=str(exc))
            raise
