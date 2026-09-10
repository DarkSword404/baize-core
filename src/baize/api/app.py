"""Baize Web API 主应用（独立实现）。

基于 FastAPI 提供浏览器/服务器架构下的核心能力：
- 健康检查、模型配置、智能体/工具列表
- 会话管理（创建、列表、删除）
- 流式对话
- 认证（启动生成凭证）
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import os
import sys
from dataclasses import replace as _dataclass_replace
from importlib.metadata import entry_points
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from baize import __version__

logger = logging.getLogger(__name__)
from baize.api.attachments import (
    AttachmentStore,
    attachment_tools,
    detect_file_type,
)
from baize.api.auth import AuthManager
from baize.api.custom_agents import CustomAgentStore, CustomPipelineStore, get_deleted_store
from baize.api.receivers import router as receivers_router
from baize.api.sessions import SessionManager
from baize.agents import list_agents, list_tools, get_agent
from baize.agents.guardrails import (
    GuardrailConfig,
    GuardrailRule,
    GuardrailSettings,
    GuardrailStore,
    SSRFGuardrailSettings,
    check_input_guardrail,
    test_guardrail,
    validate_guardrail_config,
)
from baize.config import DEFAULT_BAIZE_DIR, ModelConfigStore, SingleModelConfig, get_server_config
from baize.db_recorder import DbRecorder
from baize.multimodal import build_user_message
from baize.receivers.manager import ReceiverManager
from baize.receivers.webhook import handle_webhook
from baize.sdk.client import LLMClient, ModelNotConfiguredError, ChatMessage
from baize.sdk.session_log import SessionLog
from baize.tools.custom_tools import CustomToolStore, test_custom_tool
from baize.tools.extended import _check_url_allowed
from baize.tools.shared_browser import get_shared_browser
from baize.memory import MemoryService


# ----------------------------------------------------------------------
# Pydantic 模型
# ----------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str


class ModelConfigRequest(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    context_max_turns: int = 0
    context_window: Optional[int] = None
    max_context_tokens: Optional[int] = None
    max_message_chars: Optional[int] = None
    enable_context_summary: bool = False


class ModelConfigResponse(BaseModel):
    base_url: str
    api_key: str
    model: str
    context_max_turns: int = 0
    context_window: Optional[int] = None
    max_context_tokens: Optional[int] = None
    max_message_chars: Optional[int] = None
    enable_context_summary: bool = False
    configured: bool


class CreateSessionRequest(BaseModel):
    agent: Optional[str] = None
    model: Optional[str] = None
    stateful: bool = True
    pattern: Optional[str] = None
    browser_collab: bool = False
    # 协作模式（黑板驱动）：目标范围 + 成功条件
    # 当 scope/goal 非空时，会话进入协作模式，黑板初始化 origin/goal 节点
    # agent 可留空，由黑板根据 Fact-Intent 图状态动态派发
    scope: str = ""
    goal: str = ""


class BlackboardHintRequest(BaseModel):
    """黑板 Hint 注入请求：人类判断，下次读取被 agent 吸收。"""
    label: str
    detail: str = ""


# 全局 app state 引用（供编排进程反查会话黑板，见 reason 节点）
_APP_STATE_REF = None


def _get_app_state():
    """供编排节点（如 Reason）反查 app state，拿 session_manager → 黑板。"""
    return _APP_STATE_REF


class MessageRequest(BaseModel):
    input: str
    agent: Optional[str] = None
    # 本次消息附带的附件 file_id 列表（已上传到会话的附件）
    attachments: list[str] = Field(default_factory=list)


# ---- 长期记忆（全新 memory 子系统）请求模型 --------------------------------
class MemoryExperienceCreate(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    kind: str = "method"        # method | lesson | intel
    scope: str = "global"       # global | agent:<key>
    agent_key: str = ""
    source_session_id: str = ""
    importance: int = 3


class MemoryExperienceUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    kind: Optional[str] = None
    importance: Optional[int] = None
    note: str = ""


class MemoryStatusRequest(BaseModel):
    status: str = "active"      # draft | active | superseded | invalidated
    note: str = ""


class MemoryFeedbackRequest(BaseModel):
    useful: bool = True         # True=采纳/有用，False=无用（连续无用会自动降级为草稿）
    note: str = ""


class MemoryConsolidateRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    auto_commit: bool = False   # True 时直接取代旧条目


class AuthResponse(BaseModel):
    token: str | None = None
    ok: bool


class LoginRequest(BaseModel):
    username: str
    password: str


class SandboxCheckRequest(BaseModel):
    tool_name: str
    session_id: str


class SandboxApproveRequest(BaseModel):
    tool_name: str
    session_id: str


class SandboxDenyRequest(BaseModel):
    tool_name: str
    session_id: str


class SandboxPolicyRequest(BaseModel):
    enabled: bool = True
    default_permission: str = "approve"
    tool_permissions: dict[str, str] = Field(default_factory=dict)
    auto_approve_after: int = 3
    max_dangerous_per_turn: int = 10


class ListSessionsResponse(BaseModel):
    sessions: list[dict]


class AgentsResponse(BaseModel):
    agents: list[dict]


class ToolsResponse(BaseModel):
    tools: list[dict]


class CustomToolCreateRequest(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: str = ""
    category: str = "custom"
    code: str
    parameters: Optional[dict] = None
    enabled: bool = True


class CustomToolUpdateRequest(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    code: Optional[str] = None
    parameters: Optional[dict] = None
    enabled: Optional[bool] = None


class CustomToolToggleRequest(BaseModel):
    enabled: bool


class CustomToolTestRequest(BaseModel):
    code: str
    args: Optional[dict] = None
    timeout: Optional[int] = 60


class SSRFGuardrailSettingsRequest(BaseModel):
    """SSRF 护栏配置（运行时即时生效，JSON 持久化）。"""

    enabled: bool = True
    block_private: bool = True
    block_loopback: bool = True
    block_link_local: bool = True
    block_reserved: bool = True
    block_multicast: bool = True
    block_unspecified: bool = True
    allowlist_cidrs: list[str] = Field(default_factory=list)
    allowlist_hosts: list[str] = Field(default_factory=list)


class GuardrailSettingsRequest(BaseModel):
    input_enabled: bool = True
    output_enabled: bool = False
    max_input_length: int = 16384
    ssrf: SSRFGuardrailSettingsRequest = Field(default_factory=SSRFGuardrailSettingsRequest)


class GuardrailRuleRequest(BaseModel):
    id: str
    name: str
    category: str = "input_injection"
    description: str = ""
    severity: str = "medium"
    kind: str = "regex"
    pattern: str = ""
    enabled: bool = True


class GuardrailConfigRequest(BaseModel):
    settings: GuardrailSettingsRequest = Field(default_factory=GuardrailSettingsRequest)
    rules: list[GuardrailRuleRequest] = Field(default_factory=list)


class GuardrailTestRequest(BaseModel):
    text: str
    kind: str = "input"


class SharedBrowserOpenRequest(BaseModel):
    url: str = Field(..., description="要打开的 URL")


class SharedBrowserClickRequest(BaseModel):
    """按视口坐标点击共享浏览器页面（前端截图交互层映射后的坐标）。"""

    x: float = Field(..., ge=0, description="视口 X 坐标")
    y: float = Field(..., ge=0, description="视口 Y 坐标")
    button: str = Field("left", description="鼠标按键: left/right/middle")
    click_count: int = Field(1, ge=1, le=3, description="点击次数（2 为双击）")


class SharedBrowserTypeRequest(BaseModel):
    """向共享浏览器当前聚焦元素输入文本。"""

    text: str = Field(..., description="要输入的文本")


class SharedBrowserKeyRequest(BaseModel):
    """在共享浏览器中按下指定按键。"""

    key: str = Field(..., description="Playwright 键名，如 Enter/Tab/Backspace/Escape/ArrowUp")


class SharedBrowserScrollRequest(BaseModel):
    """滚动共享浏览器当前页面。"""

    delta_x: float = Field(0, description="水平滚动量")
    delta_y: float = Field(0, description="垂直滚动量")


class SharedBrowserNavRequest(BaseModel):
    """共享浏览器导航动作。"""

    action: str = Field(..., description="back / forward / reload")


# ----------------------------------------------------------------------
# 认证依赖
# ----------------------------------------------------------------------
def _require_api_key(request: Request) -> None:
    auth_manager: AuthManager = request.app.state.auth_manager
    if not request.app.state.require_auth:
        return
    # 仅接受请求头携带的 Token；不支持 URL 参数（避免泄露到浏览器历史/日志）
    key = request.headers.get("X-Baize-API-Key") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth_manager.validate_token(key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或缺失 API Token",
        )


# ----------------------------------------------------------------------
# SSE 心跳包装
# ----------------------------------------------------------------------
def _is_continue_intent(text: str) -> bool:
    """判断用户输入是否为"继续/接续"类指令（用于中断后基于已有上下文续跑）。"""
    t = (text or "").strip().lower()
    if not t or len(t) > 20:
        return False
    if t in ("继续", "继续执行", "继续吧", "继续干活", "接着", "接着来", "接续", "continue", "keep going", "go on", "continue the task", "continue执行"):
        return True
    return any(t.startswith(k) for k in ("继续", "接着", "continue", "keep going"))


def _rebuild_prior_history(history_messages: list[dict]) -> list[ChatMessage]:
    """将会话持久化消息重建为传给模型的完整 ChatMessage 历史。

    必须保留已执行的工具调用链（function_call / function_call_output），
    否则中断后用户说"继续"时，模型看不到已执行到哪一步，只能从头重新执行。
    """
    prior_history: list[ChatMessage] = []
    last_tool_call_id: str | None = None  # 最近一个 function_call 的 id（用于配对）
    pending_tool_calls = 0  # 尚未配对的 function_call 数量
    for m in history_messages:
        role = m.get("role")
        mtype = m.get("type")
        if role == "user" and not mtype:
            prior_history.append(ChatMessage(role="user", content=m.get("content", "")))
        elif role == "assistant" and not mtype:
            prior_history.append(ChatMessage(role="assistant", content=m.get("content", "")))
        elif mtype == "function_call":
            pending_tool_calls += 1
            last_tool_call_id = m.get("id") or f"call_{pending_tool_calls}"
            args = m.get("arguments")
            if isinstance(args, (dict, list)):
                args_str = json.dumps(args, ensure_ascii=False)
            else:
                args_str = str(args or "{}")
            prior_history.append(
                ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        {
                            "id": last_tool_call_id,
                            "type": "function",
                            "function": {
                                "name": m.get("name", ""),
                                "arguments": args_str,
                            },
                        }
                    ],
                )
            )
        elif mtype == "function_call_output":
            # 旧数据可能没有 id：复用最近一个 function_call 的 id 完成配对
            pending_tool_calls = max(0, pending_tool_calls - 1)
            prior_history.append(
                ChatMessage(
                    role="tool",
                    content=m.get("output", ""),
                    tool_call_id=m.get("id") or last_tool_call_id or "call_0",
                    name=m.get("name", ""),
                )
            )
    # 兜底：若最后存在孤立的 function_call（中断时工具尚未返回结果），
    # 补一条 tool 消息，保证 OpenAI 格式中 assistant(tool_calls) 与 tool 成对。
    if pending_tool_calls > 0:
        prior_history.append(
            ChatMessage(
                role="tool",
                content="（中断：工具执行未返回结果）",
                tool_call_id=last_tool_call_id or "call_0",
            )
        )
    return prior_history


def _model_config_hint() -> str:
    """返回当前模型配置概要（供报错上下文），不可用时返回空串。"""
    try:
        from baize.sdk.client import get_active_model_config

        cfg = get_active_model_config()
        if cfg is not None and getattr(cfg, "base_url", ""):
            return f"(base_url={cfg.base_url})"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _format_detailed_error(exc: Exception, phase: str = "对话处理") -> str:
    """将异常转换为详细、可运维的报错文案（通过 SSE error 事件回显给前端）。

    相比旧的「服务器内部错误，请查看服务端日志」，这里输出异常类型、
    关键上下文与可操作排查建议，便于运维直接定位问题，无需翻阅服务端日志。
    """
    exc_type = type(exc).__name__

    if isinstance(exc, ModelNotConfiguredError):
        return f"{phase}失败: {exc}"

    from httpx import (
        ConnectError,
        ConnectTimeout,
        HTTPStatusError,
        ReadError,
        ReadTimeout,
        RemoteProtocolError,
    )

    # OpenAI/httpx 客户端常把底层网络错误包在 __cause__/__context__ 里
    cause = exc.__cause__ or exc.__context__
    net_types = (ConnectError, ConnectTimeout, ReadError, ReadTimeout, RemoteProtocolError, ConnectionError, TimeoutError)
    target = cause if isinstance(cause, net_types) else exc

    if isinstance(target, net_types):
        reason = str(cause if isinstance(cause, net_types) else exc)
        base = _model_config_hint() or "（未获取到 base_url，请检查模型配置）"
        return (
            f"{phase}失败: LLM API 连接异常 [{exc_type}] {reason}\n"
            f"当前模型配置: {base}\n"
            f"排查建议: ① 用 curl -v {base} 检查 LLM 端点连通性；② 确认 api_key/model 正确；"
            f"③ 确认 LLM 服务已启动、端口未被防火墙拦截；④ 检查网络代理/环境变量是否影响请求。"
        )

    if isinstance(target, HTTPStatusError):
        resp = getattr(target, "response", None)
        status_code = getattr(resp, "status_code", "?")
        body = (getattr(resp, "text", "") or "")[:300]
        base = _model_config_hint()
        return (
            f"{phase}失败: LLM API 返回 HTTP {status_code}{base}\n"
            f"响应内容: {body or '(空)'}\n"
            f"排查建议: 401→检查 api_key；403→检查账户权限/配额；429→触发限流，稍后重试或降低并发；"
            f"400→检查请求参数/模型名；5xx→LLM 服务端故障，检查其日志。"
        )

    try:
        from openai import AuthenticationError, PermissionDeniedError, RateLimitError

        if isinstance(target, AuthenticationError):
            return f"{phase}失败: LLM API 认证失败 (401){_model_config_hint()}，请检查 api_key 是否正确有效。"
        if isinstance(target, PermissionDeniedError):
            return f"{phase}失败: LLM API 权限不足 (403){_model_config_hint()}，请检查账户权限/额度。"
        if isinstance(target, RateLimitError):
            return f"{phase}失败: LLM API 触发限流 (429){_model_config_hint()}，请稍后重试或降低请求频率。"
    except ImportError:  # 未安装 openai 时跳过精细化分类
        pass

    # 兜底：输出异常类型 + 消息 + 排查入口
    detail = str(exc).replace("\n", " ")[:400]
    return (
        f"{phase}失败 [{exc_type}]: {detail or '(无详细信息)'}\n"
        f"排查入口: ① 查看服务端日志 logs/backend.log（含完整 traceback）；② 运行 baize doctor 检查环境/工具/模型配置。"
    )


async def _with_sse_heartbeat(agen, interval: float = 15.0):
    """包装异步生成器，静默期定期产出心跳，防止长时工具执行导致连接超时断开。

    产出形式为 ``(kind, item)``：
    - ``("event", event)``：上游生成器产出的原始事件
    - ``("heartbeat", None)``：超过 ``interval`` 秒无事件时产出的心跳标记

    调用方对心跳标记应输出 SSE 注释行（``: keepalive``），不触发前端事件，
    但能维持 TCP 连接活跃，避免代理/网络设备因空闲超时切断会话。
    """
    next_task: asyncio.Task | None = None
    sleep_task: asyncio.Task | None = None
    try:
        while True:
            if next_task is None:
                next_task = asyncio.ensure_future(agen.__anext__())
            if sleep_task is None:
                sleep_task = asyncio.ensure_future(asyncio.sleep(interval))
            done, _ = await asyncio.wait(
                {next_task, sleep_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if next_task in done:
                if sleep_task is not None and not sleep_task.done():
                    sleep_task.cancel()
                sleep_task = None
                try:
                    item = next_task.result()
                except StopAsyncIteration:
                    return
                next_task = None
                yield "event", item
            else:
                yield "heartbeat", None
                sleep_task = None
    finally:
        for t in (next_task, sleep_task):
            if t is not None and not t.done():
                t.cancel()
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------
# 会话审计日志：SessionLog 生产接线辅助
# ----------------------------------------------------------------------
def _get_or_create_session_log(app: FastAPI, session_id: str) -> SessionLog:
    """取（或建）会话级 SessionLog，JSONL 落盘 ~/.baize/sessions/<id>.audit.jsonl。

    同一会话的多轮消息共享同一日志（进程内缓存 + 磁盘续写），
    提供 append-only 审计事实源；删除会话时由 delete_session 同步清理。
    """
    logs: dict[str, SessionLog] = app.state.session_logs
    log = logs.get(session_id)
    if log is None:
        path = os.path.join(app.state.session_log_dir, f"{session_id}.audit.jsonl")
        log = SessionLog(session_id=session_id, path=path, origin="api")
        logs[session_id] = log
    return log


def _clone_agent_for_session(agent: Any, session_id: str, session_log: SessionLog) -> Any:
    """克隆 agent 运行副本并绑定会话级 session_id / session_log。

    注册表内的 agent 是全局共享单例，直接写字段会污染并发请求
    （session 互相串台）。通过 dataclasses.replace 产生浅拷贝副本，
    仅替换会话相关字段，其余（tools/memory/hooks 等）仍共享定义。
    """
    try:
        return _dataclass_replace(
            agent, session_id=session_id, session_log=session_log
        )
    except TypeError:
        # 非 dataclass 的兜底 agent：浅拷贝后绑定（保持单例不被修改）
        copied = copy.copy(agent)
        copied.session_id = session_id
        copied.session_log = session_log
        return copied


# ----------------------------------------------------------------------
# 应用工厂
# ----------------------------------------------------------------------
def create_baize_api_app(
    *,
    session_manager: SessionManager | None = None,
) -> FastAPI:
    # 配置 application logger：uvicorn 直接启动时不经过 cli.py，
    # 需在此显式配置，否则 baize.orchestration 节点的日志不可见。
    import sys as _sys
    _log_level = os.environ.get("BAIZE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, _log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=_sys.stdout,
    )
    for _ns in ("baize", "baize.orchestration", "baize.api", "baize.sdk"):
        logging.getLogger(_ns).setLevel(getattr(logging, _log_level, logging.INFO))

    cfg = get_server_config()
    app = FastAPI(title="Baize API", version=__version__)

    # CORS（允许前端开发服务器）
    # 注意：前端使用 X-Baize-API-Key 请求头认证（非 Cookie），
    # 因此 allow_credentials 必须为 False —— "*" + credentials=True 在浏览器规范下无效。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 全局状态
    app.state.session_manager = session_manager or SessionManager()
    app.state.model_config = ModelConfigStore()
    app.state.auth_manager = AuthManager()
    app.state.custom_agents = CustomAgentStore()
    app.state.custom_pipelines = CustomPipelineStore()
    app.state.deleted_store = get_deleted_store()
    app.state.custom_tools = CustomToolStore()

    # ── 沙箱边界系统 ──
    from baize.sandbox import Sandbox, SandboxPolicy, PermissionLevel
    from baize import services

    # 默认权限改为 ALLOW：pipeline 路径中无人审批，APPROVE 会导致 agent 阻塞。
    # 危险工具仍受 max_dangerous_per_turn 配额限制。
    app.state.sandbox = Sandbox(SandboxPolicy(
        enabled=True,
        default_permission=PermissionLevel.ALLOW,
    ))

    app.state.custom_tools.register_all()  # 启动时热注册已有自定义工具
    app.state.attachment_store = AttachmentStore()
    app.state.require_auth = cfg.require_auth
    app.state.loaded_modules: dict[str, dict] = {}  # 已加载模块注册表

    # 全局 app state 引用：供编排进程（reason 节点等）反查会话黑板
    global _APP_STATE_REF
    _APP_STATE_REF = app.state
    # ── 长期记忆：memory 子系统（Episode/经验/语义事实/时间知识图谱）──
    # 全新实现（baize.memory），取代历史上所有经验引擎。Agent 每回合结束自动
    # 学习（见 sdk.agent._try_auto_refine），此处只负责注册全局服务。
    app.state.memory_service = MemoryService()

    # ── 会话审计日志：SessionLog JSONL 落盘目录 + 进程内缓存 ──
    app.state.session_log_dir = os.path.join(str(DEFAULT_BAIZE_DIR), "sessions")
    app.state.session_logs: dict[str, SessionLog] = {}
    # DbRecorder（SQLite 结构化镜像）注册为全局服务，
    # Agent._log_event 会把每条会话事件并行写入 runtime.sqlite
    app.state.db_recorder = DbRecorder()
    services.register("db_recorder", app.state.db_recorder)

    # 注册到全局服务表，供 Agent 运行时查找
    services.register("sandbox", app.state.sandbox)
    services.register("memory_service", app.state.memory_service)

    # ------------------------------------------------------------------
    # 启动凭证输出
    # ------------------------------------------------------------------
    _print_credentials(app, cfg)

    # ------------------------------------------------------------------
    # 模块发现：加载所有 baize.modules entry points
    # ------------------------------------------------------------------
    _discover_and_load_modules(app)

    # ------------------------------------------------------------------
    # 接收器管理 API + Webhook 路由
    # ------------------------------------------------------------------
    app.include_router(receivers_router, prefix="/api/v1")

    @app.on_event("startup")
    async def _start_receiver_manager():
        """应用启动时初始化 ReceiverManager 并启动所有已启用的接收器。"""
        mgr = ReceiverManager.get()
        await mgr.start()
        logger.info("ReceiverManager 已启动")

    @app.on_event("shutdown")
    async def _stop_receiver_manager():
        """应用关闭时停止所有接收器。"""
        mgr = ReceiverManager.get()
        await mgr.stop()

    # Webhook 捕获所有路由
    @app.api_route("/api/v1/hook/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def webhook_catchall(request: Request, path: str):
        return await handle_webhook(request, path)

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------
    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    # ------------------------------------------------------------------
    # 已安装模块列表
    # ------------------------------------------------------------------
    @app.get("/api/v1/modules")
    def modules_list() -> dict:
        """返回已安装并可用的模块列表。前端据此动态显示/隐藏功能。"""
        return {"modules": app.state.loaded_modules}

    # ------------------------------------------------------------------
    # 共享协作浏览器（人机共用可视化浏览器）
    # ------------------------------------------------------------------

    @app.get(
        "/api/v1/shared-browser/status",
        dependencies=[Depends(_require_api_key)],
    )
    def shared_browser_status() -> dict:
        """查询共享浏览器状态（是否运行 / headless / 当前 URL）。"""
        return get_shared_browser().status()

    @app.post(
        "/api/v1/shared-browser/open",
        dependencies=[Depends(_require_api_key)],
    )
    async def shared_browser_open(payload: SharedBrowserOpenRequest) -> dict:
        """在共享浏览器中打开指定 URL（SSRF 校验后导航）。"""
        url = payload.url.strip()
        try:
            _check_url_allowed(url, allow_internal=False)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"URL 校验失败: {exc}")
        try:
            result = await get_shared_browser().open(url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"打开共享浏览器失败: {exc}")
        return {"ok": True, "result": result}

    @app.post(
        "/api/v1/shared-browser/confirm",
        dependencies=[Depends(_require_api_key)],
    )
    def shared_browser_confirm() -> dict:
        """人工确认放行（唤醒 shared_browser_wait_user，用于扫码登录后继续）。"""
        get_shared_browser().confirm()
        return {"ok": True, "message": "已发送人工确认信号"}

    @app.get(
        "/api/v1/shared-browser/snapshot",
        dependencies=[Depends(_require_api_key)],
    )
    async def shared_browser_snapshot() -> Response:
        """返回共享浏览器当前页面的 PNG 截图（前端面板轮询展示）。"""
        image = await get_shared_browser().snapshot()
        if image is None:
            raise HTTPException(status_code=409, detail="共享浏览器未启动或窗口不可用")
        return Response(content=image, media_type="image/png")

    @app.post(
        "/api/v1/shared-browser/click",
        dependencies=[Depends(_require_api_key)],
    )
    async def shared_browser_click(payload: SharedBrowserClickRequest) -> dict:
        """按视口坐标点击（前端截图交互层把屏幕坐标映射为页面坐标后调用）。"""
        try:
            result = await get_shared_browser().click_viewport(
                payload.x, payload.y, button=payload.button, click_count=payload.click_count
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"点击失败: {exc}")
        return {"ok": True, "result": result}

    @app.post(
        "/api/v1/shared-browser/type",
        dependencies=[Depends(_require_api_key)],
    )
    async def shared_browser_type(payload: SharedBrowserTypeRequest) -> dict:
        """向当前聚焦元素输入文本。"""
        try:
            result = await get_shared_browser().type_text(payload.text)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"输入失败: {exc}")
        return {"ok": True, "result": result}

    @app.post(
        "/api/v1/shared-browser/key",
        dependencies=[Depends(_require_api_key)],
    )
    async def shared_browser_key(payload: SharedBrowserKeyRequest) -> dict:
        """按下指定按键。"""
        try:
            result = await get_shared_browser().press_key(payload.key)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"按键失败: {exc}")
        return {"ok": True, "result": result}

    @app.post(
        "/api/v1/shared-browser/scroll",
        dependencies=[Depends(_require_api_key)],
    )
    async def shared_browser_scroll(payload: SharedBrowserScrollRequest) -> dict:
        """滚动当前页面。"""
        try:
            result = await get_shared_browser().scroll(payload.delta_x, payload.delta_y)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"滚动失败: {exc}")
        return {"ok": True, "result": result}

    @app.post(
        "/api/v1/shared-browser/nav",
        dependencies=[Depends(_require_api_key)],
    )
    async def shared_browser_nav(payload: SharedBrowserNavRequest) -> dict:
        """浏览器后退/前进/刷新。"""
        try:
            result = await get_shared_browser().nav(payload.action)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"导航失败: {exc}")
        return {"ok": True, "result": result}

    @app.post(
        "/api/v1/shared-browser/close",
        dependencies=[Depends(_require_api_key)],
    )
    async def shared_browser_close() -> dict:
        """关闭共享浏览器（保留登录态）。"""
        result = await get_shared_browser().close()
        return {"ok": True, "result": result}

    # ---- 共享浏览器 CDP 实时帧流 + 输入注入（WebSocket 双向）----------
    async def _require_ws_api_key(ws: WebSocket) -> bool:
        """WebSocket 鉴权：query `token` 或 `X-Baize-API-Key` 头。失败 close(4401)。"""
        if not ws.app.state.require_auth:
            return True
        token = ws.query_params.get("token") or ws.headers.get("x-baize-api-key")
        if not ws.app.state.auth_manager.validate_token(token or ""):
            await ws.close(code=4401)
            return False
        return True

    async def _sb_dispatch(
        sb: Any, msg: dict, send_frame: Any, send_status: Any
    ) -> None:
        """按 msg.t 路由到共享浏览器的输入注入/导航方法。"""
        t = msg.get("t")
        if t == "open":
            await sb.open(str(msg.get("url", "")))
            if not sb.is_streaming():
                await sb.start_stream(send_frame, send_status)
            await send_status()
        elif t == "nav":
            await sb.nav(str(msg.get("action", "reload")))
            await send_status()
        elif t == "close":
            await sb.close()
            await send_status()
        elif t == "confirm":
            sb.confirm()
        elif t == "mouseMove":
            await sb.input_mouse_move(float(msg["x"]), float(msg["y"]))
        elif t == "mouseDown":
            await sb.input_mouse_down(float(msg["x"]), float(msg["y"]), msg.get("button", "left"), int(msg.get("clickCount", 1)))
        elif t == "mouseUp":
            await sb.input_mouse_up(float(msg["x"]), float(msg["y"]), msg.get("button", "left"), int(msg.get("clickCount", 1)))
        elif t == "click":
            await sb.input_click(float(msg["x"]), float(msg["y"]), msg.get("button", "left"), int(msg.get("count", 1)))
        elif t == "wheel":
            await sb.input_wheel(float(msg.get("dx", 0)), float(msg.get("dy", 0)))
        elif t == "key":
            await sb.input_key(str(msg["key"]))
        elif t == "type":
            await sb.input_type(str(msg.get("text", "")))
            logger.debug(f"[ws] type received: {msg.get('text', '')[:20]!r}")

    @app.websocket("/api/v1/shared-browser/stream")
    async def shared_browser_stream(ws: WebSocket) -> None:
        """CDP 实时帧流（下行）+ 输入事件注入（上行）双向 WebSocket。

        鉴权：query `token` 或 `X-Baize-API-Key` 头。
        下行：`{t:"frame",d:base64jpeg,w,h}` / `{t:"status",...}` / `{t:"error",msg}`。
        上行：open/nav/close/confirm/mouseMove/mouseDown/mouseUp/click/wheel/key/type。
        """
        if not await _require_ws_api_key(ws):
            return
        await ws.accept()
        sb = get_shared_browser()

        async def _send_frame(data: bytes, w: int, h: int) -> None:
            await ws.send_json({"t": "frame", "d": base64.b64encode(data).decode(), "w": w, "h": h})

        async def _send_status() -> None:
            await ws.send_json({"t": "status", **sb.status()})

        try:
            await _send_status()
            if sb.is_running() and not sb.is_streaming():
                await sb.start_stream(_send_frame, _send_status)
            while True:
                msg = await ws.receive_json()
                await _sb_dispatch(sb, msg, _send_frame, _send_status)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("shared-browser stream ws error: %s", exc)
            try:
                await ws.send_json({"t": "error", "msg": str(exc)})
            except Exception:  # noqa: BLE001
                pass
        finally:
            await sb.stop_stream()

    # ------------------------------------------------------------------
    # 模型配置（单模型）
    # ------------------------------------------------------------------
    @app.get(
        "/api/v1/model-config",
        response_model=ModelConfigResponse,
        dependencies=[Depends(_require_api_key)],
    )
    def get_model_config() -> ModelConfigResponse:
        m = app.state.model_config.load()
        if m is None:
            return ModelConfigResponse(
                base_url="", api_key="", model="", context_max_turns=0, configured=False
            )
        return ModelConfigResponse(
            base_url=m.base_url,
            api_key=m.api_key,
            model=m.model,
            context_max_turns=int(m.context_max_turns or 0),
            context_window=m.context_window,
            max_context_tokens=m.max_context_tokens,
            max_message_chars=m.max_message_chars,
            enable_context_summary=bool(m.enable_context_summary),
            configured=True,
        )

    @app.put(
        "/api/v1/model-config",
        response_model=ModelConfigResponse,
        dependencies=[Depends(_require_api_key)],
    )
    def update_model_config(payload: ModelConfigRequest) -> ModelConfigResponse:
        base_url = payload.base_url.strip().rstrip("/")
        model = payload.model.strip()
        if not base_url or not model:
            raise HTTPException(status_code=400, detail="base_url 和 model 不能为空")
        context_max_turns = int(payload.context_max_turns or 0)
        if context_max_turns < 0:
            raise HTTPException(status_code=400, detail="context_max_turns 不能为负数")
        # 负数值一律按未配置（None）处理；0 表示"不限制"
        for name, value in (
            ("context_window", payload.context_window),
            ("max_context_tokens", payload.max_context_tokens),
            ("max_message_chars", payload.max_message_chars),
        ):
            if value is not None and value < 0:
                raise HTTPException(status_code=400, detail=f"{name} 不能为负数")
        m = app.state.model_config.save(
            SingleModelConfig(
                base_url=base_url,
                api_key=payload.api_key.strip(),
                model=model,
                context_max_turns=context_max_turns,
                context_window=payload.context_window,
                max_context_tokens=payload.max_context_tokens,
                max_message_chars=payload.max_message_chars,
                enable_context_summary=bool(payload.enable_context_summary),
            )
        )
        return ModelConfigResponse(
            base_url=m.base_url,
            api_key=m.api_key,
            model=m.model,
            context_max_turns=int(m.context_max_turns or 0),
            context_window=m.context_window,
            max_context_tokens=m.max_context_tokens,
            max_message_chars=m.max_message_chars,
            enable_context_summary=bool(m.enable_context_summary),
            configured=True,
        )

    @app.delete(
        "/api/v1/model-config",
        dependencies=[Depends(_require_api_key)],
    )
    def clear_model_config() -> dict:
        app.state.model_config.clear()
        return {"ok": True, "configured": False}

    # ------------------------------------------------------------------
    # 智能体 / 工具
    # ------------------------------------------------------------------

    def _build_custom_agent(custom_agents: CustomAgentStore, name: str):
        """从自定义智能体存储构造可执行的 Agent 实例（内置注册表查不到时使用）。

        将自定义智能体的指令/模型/工具映射为运行时 Agent。
        """
        from baize.sdk.agent import Agent
        from baize.tools import extended_tools

        custom = custom_agents.find_by_name(name)
        if custom is None:
            return None
        tool_map = {t.name: t for t in extended_tools()}
        tools = [tool_map[t] for t in (custom.get("tools") or []) if t in tool_map]
        return Agent(
            name=custom.get("name", name),
            description=custom.get("description", ""),
            instructions=custom.get("instructions", ""),
            model=custom.get("model") or None,
            tools=tools,
        )

    @app.get(
        "/api/v1/agents",
        response_model=AgentsResponse,
        dependencies=[Depends(_require_api_key)],
    )
    def agents_list() -> AgentsResponse:
        # 统一返回内置（过滤已删除）+ 自定义智能体，保证列表数据源唯一
        deleted = get_deleted_store()
        builtin = [
            {**a, "is_custom": False}
            for a in list_agents()
            if not deleted.is_agent_deleted(a["name"])
        ]
        custom = []
        for a in app.state.custom_agents.list():
            custom.append(
                {
                    "id": a.get("id", a.get("name", "")),
                    "name": a.get("name", ""),
                    "description": a.get("description", ""),
                    "instructions": a.get("instructions", ""),
                    "model": a.get("model", ""),
                    "type": "agent",
                    "pattern_type": None,
                    "source": "custom",
                    "is_custom": True,
                    "tools": [
                        {"name": t, "description": ""} for t in (a.get("tools") or [])
                    ],
                }
            )
        return AgentsResponse(agents=[*builtin, *custom])

    # ------------------------------------------------------------------
    # 自定义智能体 CRUD — 必须注册在 /api/v1/agents/{agent_name} 之前，
    # 否则 "custom" 会被 {agent_name} 动态路由抢先匹配而返回 404
    # ------------------------------------------------------------------
    @app.get(
        "/api/v1/agents/custom",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def custom_agents_list() -> dict:
        return {"agents": app.state.custom_agents.list()}

    @app.post(
        "/api/v1/agents/custom",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def custom_agents_create(payload: dict) -> dict:
        return app.state.custom_agents.create(dict(payload))

    @app.put(
        "/api/v1/agents/custom/{agent_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def custom_agents_update(agent_id: str, payload: dict) -> dict:
        agent = app.state.custom_agents.update(agent_id, dict(payload))
        if agent is None:
            raise HTTPException(status_code=404, detail="自定义智能体不存在")
        return agent

    @app.delete(
        "/api/v1/agents/custom/{agent_id}",
        dependencies=[Depends(_require_api_key)],
    )
    def custom_agents_delete(agent_id: str) -> dict:
        ok = app.state.custom_agents.delete(agent_id)
        if not ok:
            raise HTTPException(status_code=404, detail="自定义智能体不存在")
        return {"success": True}

    @app.get(
        "/api/v1/agents/{agent_name}",
        dependencies=[Depends(_require_api_key)],
    )
    def agent_detail(agent_name: str) -> dict:
        """获取智能体详情，包含 instructions 等完整信息。"""
        agent = get_agent(agent_name)
        if agent is not None:
            return {
                "name": agent.name,
                "id": agent.name,
                "description": getattr(agent, "description", ""),
                "instructions": getattr(agent, "instructions", ""),
                "source": "builtin",
                "type": "agent",
                "is_custom": False,
                "tools": [{"name": t.name, "description": t.description} for t in agent.tools],
            }
        # 自定义智能体
        custom = app.state.custom_agents.find_by_name(agent_name)
        if custom is None:
            raise HTTPException(status_code=404, detail=f"智能体 '{agent_name}' 未找到")
        return {
            "name": custom.get("name", agent_name),
            "id": custom.get("id", agent_name),
            "description": custom.get("description", ""),
            "instructions": custom.get("instructions", ""),
            "model": custom.get("model", ""),
            "source": "custom",
            "type": "agent",
            "pattern_type": None,
            "is_custom": True,
            "tools": [
                {"name": t, "description": ""} for t in (custom.get("tools") or [])
            ],
        }

    @app.delete(
        "/api/v1/agents/{agent_name}",
        dependencies=[Depends(_require_api_key)],
    )
    def delete_agent(agent_name: str) -> dict:
        """删除内置智能体（软删除，可恢复）。"""
        deleted = get_deleted_store()
        # 确认智能体存在
        from baize.agents import list_agents as _raw_agents
        all_agents = [a["name"] for a in _raw_agents()]
        if agent_name not in all_agents and deleted.is_agent_deleted(agent_name):
            return {"error": f"智能体 '{agent_name}' 未找到", "ok": False}
        deleted.delete_agent(agent_name)
        return {"ok": True, "agent_name": agent_name, "message": "智能体已删除"}

    @app.post(
        "/api/v1/agents/reset",
        dependencies=[Depends(_require_api_key)],
    )
    def reset_agents() -> dict:
        """恢复所有已删除的内置智能体。"""
        deleted = get_deleted_store()
        count = deleted.reset_agents()
        return {"ok": True, "restored": count, "message": f"已恢复 {count} 个智能体"}

    @app.get(
        "/api/v1/tools",
        response_model=ToolsResponse,
        dependencies=[Depends(_require_api_key)],
    )
    def tools_list() -> ToolsResponse:
        builtin = list_tools()
        custom = [
            {
                "name": r["name"],
                "description": r.get("description", ""),
                "category": r.get("category", "custom"),
                "is_custom": True,
                "enabled": r.get("enabled", True),
            }
            for r in app.state.custom_tools.list()
        ]
        return ToolsResponse(tools=builtin + custom)

    # ------------------------------------------------------------------
    # 自定义工具管理 (Custom Tools)
    # ------------------------------------------------------------------
    @app.get(
        "/api/v1/tools/custom",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def custom_tools_list() -> dict:
        return {"tools": app.state.custom_tools.list()}

    @app.post(
        "/api/v1/tools/custom",
        response_model=dict,
        status_code=201,
        dependencies=[Depends(_require_api_key)],
    )
    async def custom_tools_create(payload: CustomToolCreateRequest) -> dict:
        try:
            record = app.state.custom_tools.create(payload.model_dump(exclude_none=True))
            return {"ok": True, "tool": record}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put(
        "/api/v1/tools/custom/{tool_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    async def custom_tools_update(tool_id: str, payload: CustomToolUpdateRequest) -> dict:
        try:
            record = app.state.custom_tools.update(
                tool_id, payload.model_dump(exclude_none=True)
            )
            return {"ok": True, "tool": record}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/tools/custom/{tool_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def custom_tools_delete(tool_id: str) -> dict:
        try:
            app.state.custom_tools.delete(tool_id)
            return {"ok": True}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/tools/custom/{tool_id}/toggle",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def custom_tools_toggle(tool_id: str, payload: CustomToolToggleRequest) -> dict:
        try:
            record = app.state.custom_tools.set_enabled(tool_id, payload.enabled)
            return {"ok": True, "tool": record}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/tools/custom/test",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    async def custom_tools_test(payload: CustomToolTestRequest) -> dict:
        result = await test_custom_tool(payload.code, payload.args, timeout=payload.timeout or 60)
        return result


    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------
    @app.post(
        "/api/v1/sessions",
        response_model=dict,
        status_code=201,
        dependencies=[Depends(_require_api_key)],
    )
    def create_session(payload: CreateSessionRequest) -> dict:
        session = app.state.session_manager.create_session(
            agent=payload.agent,
            model=payload.model,
            stateful=payload.stateful,
            pattern=payload.pattern,
            browser_collab=payload.browser_collab,
            scope=payload.scope,
            goal=payload.goal,
        )
        return session.to_dict()

    @app.get(
        "/api/v1/sessions",
        response_model=ListSessionsResponse,
        dependencies=[Depends(_require_api_key)],
    )
    def list_sessions() -> ListSessionsResponse:
        sessions = app.state.session_manager.list_sessions()
        return ListSessionsResponse(sessions=[s.to_dict() for s in sessions])

    @app.get(
        "/api/v1/sessions/{session_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def get_session(session_id: str) -> dict:
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"session": session.to_dict()}

    # ---- 黑板：协作模式攻击图 -----------------------------------------
    # 前端攻击地图视图拉取 Fact-Intent 图快照，以及人类注入 Hint
    @app.get(
        "/api/v1/sessions/{session_id}/blackboard",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def get_blackboard(session_id: str) -> dict:
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if session.blackboard is None:
            raise HTTPException(status_code=400, detail="该会话非协作模式，无黑板")
        return session.blackboard.snapshot()

    @app.post(
        "/api/v1/sessions/{session_id}/blackboard/hints",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def add_blackboard_hint(session_id: str, payload: BlackboardHintRequest) -> dict:
        """人类判断注入黑板，下次读取被 agent 吸收。"""
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if session.blackboard is None:
            raise HTTPException(status_code=400, detail="该会话非协作模式，无黑板")
        hint = session.blackboard.add_hint(label=payload.label, detail=payload.detail)
        app.state.session_manager._save(session)
        return {"ok": True, "hint": hint.to_dict()}

    @app.delete(
        "/api/v1/sessions/{session_id}",
        dependencies=[Depends(_require_api_key)],
    )
    def delete_session(session_id: str) -> dict:
        ok = app.state.session_manager.delete_session(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="会话不存在")
        # 修复：删除会话时同步清理该会话的附件、解压文件与索引
        app.state.attachment_store.delete_session(session_id)
        # 会话删除：释放进程内 SessionLog 缓存（JSONL 审计文件保留，
        # 作为 append-only 事实源；SQLite 镜像 events 亦保留可回溯）
        app.state.session_logs.pop(session_id, None)
        # 统计该会话衍生的经验条目数（经验是长期资产，不随会话删除，仅供前端提示）
        derived = [e for e in app.state.memory_service.list_experiences()
                   if e.get("source_session_id") == session_id]
        return {"ok": True, "derived_experiences": len(derived)}

    @app.post(
        "/api/v1/sessions/{session_id}/reset",
        dependencies=[Depends(_require_api_key)],
    )
    def reset_session(session_id: str) -> dict:
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        app.state.session_manager.reset_messages(session_id)
        return {"ok": True}

    @app.patch(
        "/api/v1/sessions/{session_id}/model",
        dependencies=[Depends(_require_api_key)],
    )
    def switch_session_model(session_id: str, payload: dict) -> dict:
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        model = (payload.get("model") or "").strip()
        app.state.session_manager.set_model(session_id, model)
        updated = app.state.session_manager.get_session(session_id)
        return {"session": updated.to_dict()}

    @app.patch(
        "/api/v1/sessions/{session_id}/browser-collab",
        dependencies=[Depends(_require_api_key)],
    )
    def toggle_session_browser_collab(session_id: str, payload: dict) -> dict:
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        app.state.session_manager.set_browser_collab(
            session_id, bool(payload.get("enabled", False))
        )
        updated = app.state.session_manager.get_session(session_id)
        return {"session": updated.to_dict()}

    @app.post(
        "/api/v1/sessions/{session_id}/interrupt",
        dependencies=[Depends(_require_api_key)],
    )
    def interrupt_session(session_id: str) -> dict:
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"interrupted": True, "success": True}

    @app.post(
        "/api/v1/sessions/{session_id}/cancel",
        dependencies=[Depends(_require_api_key)],
    )
    def cancel_session(session_id: str) -> dict:
        # 流式请求由前端 AbortController 中断；此处仅做会话存在性校验。
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"cancelled": True, "success": True}

    @app.post(
        "/api/v1/sessions/{session_id}/prompts/{prompt_id}/respond",
        dependencies=[Depends(_require_api_key)],
    )
    async def respond_to_prompt(
        session_id: str,
        prompt_id: str,
        payload: dict,
    ) -> dict:
        # ── 沙箱审批：prompt_id 格式 "sandbox:{session_id}:{tool_name}" ──
        if prompt_id.startswith("sandbox:"):
            parts = prompt_id[len("sandbox:"):].split(":", 1)
            if len(parts) == 2:
                sandbox_sid, tool_name = parts
                response = (payload.get("response") or payload.get("content") or "")
                rejected = payload.get("rejected")
                if rejected in ("true", True, "1") or response == "deny":
                    sandbox: Sandbox = app.state.sandbox
                    sandbox.deny(tool_name, sandbox_sid)
                    return {"ok": True, "handled": True, "approved": False}
                else:
                    sandbox: Sandbox = app.state.sandbox
                    sandbox.approve(tool_name, sandbox_sid)
                    return {"ok": True, "handled": True, "approved": True}
            return {"ok": False, "handled": False, "error": "无效的沙箱审批 prompt_id"}

        # ── 流水线人工确认：prompt_id 格式 "run:{run_id}"，桥接 runner.resume_after_confirm ──
        if prompt_id.startswith("run:"):
            run_id = prompt_id[len("run:"):]
            response = (payload.get("response") or payload.get("content") or "")
            # 用户拒绝（rejected=true）时传入拒绝信号
            rejected = payload.get("rejected")
            choice = "reject" if rejected in ("true", True, "1") else response
            try:
                from baize.orchestration.runner import get_runner
                record = await get_runner().resume_after_confirm(run_id, choice)
            except Exception as e:  # noqa: BLE001
                logger.warning("流水线人工确认失败 %s: %s", run_id, e, exc_info=True)
                return {"ok": False, "handled": False, "error": "流水线恢复失败，请查看服务端日志"}
            if record is None:
                return {"ok": False, "handled": False, "error": "流水线不存在或未处于暂停状态"}
            return {"ok": True, "handled": True, "run_id": run_id}

        # 原有逻辑：将用户的交互响应作为普通消息追加。
        response = (payload.get("response") or payload.get("content") or "")
        if response:
            app.state.session_manager.append_message(session_id, "user", f"[响应 {prompt_id}] {response}")
        return {"ok": True, "handled": False}

    # ------------------------------------------------------------------
    # 附件上传 / 列表（多模态）
    # ------------------------------------------------------------------
    @app.post(
        "/api/v1/sessions/{session_id}/files",
        status_code=201,
        dependencies=[Depends(_require_api_key)],
    )
    async def upload_attachment(
        session_id: str,
        file: UploadFile = File(...),
    ) -> dict:
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        data = await file.read()
        try:
            att = app.state.attachment_store.save_attachment(
                session_id, file.filename or "unnamed", data
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"attachment": att.to_dict(), "ok": True}

    @app.get(
        "/api/v1/sessions/{session_id}/files",
        dependencies=[Depends(_require_api_key)],
    )
    def list_attachments(session_id: str) -> dict:
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        atts = app.state.attachment_store.list_attachments(session_id)
        return {"attachments": [a.to_dict() for a in atts]}

    @app.delete(
        "/api/v1/sessions/{session_id}/files/{file_id}",
        dependencies=[Depends(_require_api_key)],
    )
    def delete_attachment(session_id: str, file_id: str) -> dict:
        ok = app.state.attachment_store.delete_attachment(session_id, file_id)
        if not ok:
            raise HTTPException(status_code=404, detail="附件不存在")
        return {"ok": True, "deleted": True}

    # ------------------------------------------------------------------
    # 对话（流式 SSE）
    # ------------------------------------------------------------------
    @app.post(
        "/api/v1/sessions/{session_id}/messages/stream",
        dependencies=[Depends(_require_api_key)],
    )
    async def stream_message(
        session_id: str,
        payload: MessageRequest,
        request: Request,
    ):
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")

        agent_name = payload.agent or session.agent
        agent = get_agent(agent_name)
        if agent is None:
            # 内置注册表查不到时，尝试自定义智能体
            agent = _build_custom_agent(app.state.custom_agents, agent_name)
        if agent is None:
            agent = get_agent(None)  # 回退默认

        # ── TokenJuice 语义压缩：为 agent 挂载工具输出压缩器 ──
        if agent is not None and agent.tool_output_compressor is None:
            from baize.compressor import CompressorConfig, ToolOutputCompressor
            agent.tool_output_compressor = ToolOutputCompressor(CompressorConfig(enabled=True))

        # ── 会话审计日志：取（或建）会话级 SessionLog，并克隆 agent 绑定 ──
        # SessionLog 每会话一份（JSONL 落盘 ~/.baize/sessions/<id>.audit.jsonl），
        # 多轮消息共享续写；agent 通过 dataclasses.replace 克隆运行副本，
        # 避免直接把 session_id/session_log 写到注册表全局单例上串台。
        session_log = _get_or_create_session_log(app, session_id)
        agent = _clone_agent_for_session(agent, session_id, session_log)

        # ── 路由判定 ──
        # 1. 显式流水线模板（pentest/vuln_scan 等）→ orchestration runner
        # 2. 对话模式（有黑板 scope/goal 但无对应模板）→ 黑板驱动的动态 agent 自动编排
        # 3. 其它 → 内置 agent 直接对话
        pipeline_def = None
        use_conversation_orchestrator = False
        if getattr(session, "pattern", None):
            try:
                from baize.orchestration.api import _find_pipeline
                pipeline_def = _find_pipeline(session.pattern)
            except Exception:  # orchestration 未安装或查找失败 → 回退
                pipeline_def = None
        # 对话模式：有黑板（scope/goal 已初始化）且未命中显式流水线模板
        if pipeline_def is None and getattr(session, "blackboard", None) is not None:
            use_conversation_orchestrator = True

        # 拼接历史（stateful 会话）：将会话已有消息作为上下文传给 Agent。
        # 关键：不能只保留 user/assistant 纯文本消息——已执行的工具调用链
        # （function_call / function_call_output 中间产物）必须转回 ChatMessage
        # 原样传回模型。否则中断后用户说"继续"时，模型看不到已执行到哪一步，
        # 只能从头重新执行整个任务。
        history_messages = app.state.session_manager.get_messages(session_id)
        prior_history = _rebuild_prior_history(history_messages)

        # ── 多模态附件处理 ──
        # 获取会话全部附件（会话级，长期可用）
        attachment_store = app.state.attachment_store
        session_attachments = attachment_store.list_attachments(session_id)
        # 解析本次消息要引用的附件（按 file_id）
        requested_ids = set(payload.attachments or [])
        active_attachments = [
            a for a in session_attachments if a.file_id in requested_ids
        ] if requested_ids else session_attachments
        # 附件访问工具（绑定当前会话）
        extra_tools = attachment_tools(attachment_store, session_id)
        # 判断节点：仅当会话开启浏览器协作时才注入共享浏览器工具，
        # 避免 AI 在普通对话中胡乱调用浏览器导致 token 消耗
        if getattr(session, "browser_collab", False):
            from baize.tools.registry import registry

            extra_tools.extend(
                spec.to_agent_tool()
                for spec in registry.all()
                if spec.name.startswith("shared_browser_")
            )
        # 多模态 user 消息（图片注入 content_parts，其它注入附件提示）
        user_chat_message = build_user_message(
            payload.input,
            active_attachments,
            attachment_store=attachment_store,
            session_id=session_id,
        )
        logger.info(
            "构建用户消息: has_content_parts=%s, attachments=%d, text_len=%d",
            user_chat_message.content_parts is not None,
            len(active_attachments),
            len(payload.input),
        )

        # ── 中断续跑：用户输入"继续"类指令且历史中存在已执行内容时，
        # 明确要求模型基于已有上下文继续，避免从头重复执行已完成步骤 ──
        if _is_continue_intent(payload.input) and prior_history:
            _hint = (
                "\n\n（用户希望继续之前中断的任务。请基于以上已执行的对话与工具结果"
                "继续处理，不要重新执行已经完成过的步骤。）"
            )
            if user_chat_message.content_parts:
                user_chat_message.content_parts.append({"type": "text", "text": _hint})
            else:
                user_chat_message.content += _hint

        # ── 长期记忆：混合检索相关既往经验/知识并注入 agent 上下文 ──
        # memory 子系统（baize.memory）：纯本地混合召回，不依赖任何旧经验引擎。
        experience_block = ""
        injected_exp_ids: list[str] = []
        try:
            recalled = app.state.memory_service.recall(payload.input)
            injected_exp_ids = recalled.get("ids", [])
            if injected_exp_ids:
                app.state.memory_service.record_hits(injected_exp_ids)
            if (recalled.get("block") or "").strip():
                experience_block = recalled["block"]
        except Exception:  # noqa: BLE001
            logger.warning("记忆检索注入失败", exc_info=True)

        async def event_source():
            sm = app.state.session_manager
            # 本轮累积缓冲：思考过程、工具调用/结果记录、最终文本
            reasoning_parts: list[str] = []
            tool_events: list[dict] = []
            final_text = ""
            # 是否已把本轮内容持久化（正常完成 / 中断都只保存一次）
            saved = False

            # ── 输入安全护栏（运行时规则即时生效） ──
            ok, guard_message = check_input_guardrail(payload.input)
            if not ok:
                logger.warning("输入被安全护栏拦截: %s", guard_message)
                yield f"data: {json.dumps({'type': 'error', 'error': f'安全护栏拦截: {guard_message}'})}\n\n"
                return

            # 流式开始前先持久化 user 提问，确保提问不因中断而丢失
            user_extra = {}
            if active_attachments:
                user_extra["attachments"] = [a.to_dict() for a in active_attachments]
            sm.append_message(session_id, "user", payload.input, extra=user_extra or None)

            def _flush_to_session():
                """将本轮已产生的中间产物与文本持久化到会话。"""
                nonlocal saved
                if saved:
                    return
                # 思考过程：作为 reasoning 中间产物消息（独立 role，避免与正常回复混淆）
                if reasoning_parts:
                    sm.append_message(
                        session_id,
                        "intermediate",
                        "",
                        extra={
                            "type": "reasoning",
                            "summary": [{"text": "".join(reasoning_parts)}],
                        },
                    )
                # 工具调用 / 结果：逐条作为 function_call / function_call_output 消息
                for ev in tool_events:
                    sm.append_message(session_id, "intermediate", "", extra=ev)
                # 最终文本：作为 assistant 正文（若有）
                if final_text.strip():
                    sm.append_message(session_id, "assistant", final_text)
                saved = True

            # ── pattern 流水线会话：提交 run 并转发 SSE 事件 ──
            # 会话绑定了流水线（人工输入类模板）时，走 orchestration runner：
            # 提交 run → 订阅事件 → 转换为前端 SSE 格式（pipeline_step / user_prompt 审批 / 文本 delta / done）
            if pipeline_def is not None:
                try:
                    from baize.orchestration.runner import get_runner
                    runner = get_runner()
                    # 缓存定义，供人工确认后 resume 时查找
                    runner.cache_pipeline(pipeline_def)

                    # context：把用户输入同时放入 text/input/message，兼容各模板的 context_schema
                    # 协作模式：注入 session_id / scope / goal，让 reason/agent 节点
                    # 能反查会话黑板，且 {{ context.scope }}/{{ context.goal }} 能渲染。
                    run_id = await runner.submit(
                        pipeline_def,
                        {
                            "text": payload.input,
                            "input": payload.input,
                            "message": payload.input,
                            "session_id": session_id,
                            "scope": getattr(session, "scope", "") or "",
                            "goal": getattr(session, "goal", "") or "",
                        },
                    )

                    node_index = {n.id: n for n in pipeline_def.nodes}
                    phase = 0
                    async for kind, event in _with_sse_heartbeat(
                        runner.subscribe_events(run_id), interval=15.0
                    ):
                        if await request.is_disconnected():
                            break
                        if kind == "heartbeat":
                            # SSE 注释行：仅维持 TCP 连接活跃
                            yield ": keepalive\n\n"
                            continue
                        etype = event.get("type", "")
                        edata = event.get("data", {}) or {}
                        if etype == "node_started":
                            node = node_index.get(edata.get("node_id", ""))
                            phase += 1
                            yield (
                                f"event: reasoning_step\n"
                                f"data: {json.dumps({'type': 'pipeline_step', 'phase': phase, 'total': len(pipeline_def.nodes), 'phase_name': getattr(node, 'display_name', '') or edata.get('node_id', ''), 'agent': getattr(node, 'agent', '')})}\n\n"
                            )
                        elif etype == "node_completed":
                            node = node_index.get(edata.get("node_id", ""))
                            # 节点文本输出（如 agent 的 final_output）优先于结构化 data 内的字段
                            node_output = edata.get("output", "") or ""
                            data = edata.get("data", {}) or {}
                            text = ""
                            if isinstance(node_output, str) and node_output.strip():
                                text = node_output
                            elif isinstance(data, dict):
                                text = (
                                    data.get("report")
                                    or data.get("text")
                                    or data.get("output")
                                    or data.get("final_output")
                                    or ""
                                )
                            if isinstance(text, str) and text.strip():
                                final_text += text
                                yield f"data: {json.dumps({'type': 'delta', 'content': text})}\n\n"
                            node_label = getattr(node, "display_name", "") or edata.get("node_id", "")
                            yield (
                                f"event: reasoning_step\n"
                                f"data: {json.dumps({'type': 'pipeline_phase_complete', 'phase': phase, 'agent': getattr(node, 'agent', ''), 'message': f'{node_label} 完成'})}\n\n"
                            )
                        elif etype == "pipeline_paused":
                            # 人工确认节点：转为 user_prompt 审批事件，前端弹窗等待用户确认
                            node = node_index.get(edata.get("node_id", ""))
                            confirm_prompt = getattr(node, "confirm_prompt", "") or "是否确认继续执行？"
                            confirm_options = getattr(node, "confirm_options", None) or ["approve", "reject"]
                            yield (
                                "event: user_prompt\n"
                                f"data: {json.dumps({'prompt_id': f'run:{run_id}', 'prompt_type': 'confirm', 'title': '人工确认', 'message': confirm_prompt, 'command': '', 'options': confirm_options, 'is_password': False})}\n\n"
                            )
                        elif etype == "pipeline_completed":
                            # pipeline 完成：不再重复发送 delta（agent 输出已在 node_completed 中推送）
                            report = (
                                edata.get("report")
                                or (edata.get("data") or {}).get("report")
                                or (edata.get("data") or {}).get("final_output")
                                or ""
                            )
                            _flush_to_session()
                            yield f"data: {json.dumps({'type': 'done', 'content': report or final_text})}\n\n"
                        elif etype == "pipeline_failed":
                            error = (
                                edata.get("error")
                                or (edata.get("data") or {}).get("error")
                                or "流水线执行失败"
                            )
                            yield f"data: {json.dumps({'type': 'error', 'error': error})}\n\n"
                        elif etype == "done":
                            break
                    _flush_to_session()
                    yield "data: [DONE]\n\n"
                    return
                except Exception as e:  # noqa: BLE001
                    logger.exception("流水线会话处理失败: %s", e)
                    yield f"data: {json.dumps({'type': 'error', 'error': _format_detailed_error(e, '流水线对话')})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

            # ── 对话模式：黑板驱动的动态 agent 自动编排（不使用流水线模板）──
            if use_conversation_orchestrator:
                try:
                    from baize.pentest.conversation_orchestrator import ConversationOrchestrator
                    orch = ConversationOrchestrator()
                    # 浏览器协作工具追加到 extra_tools（已在上方组装）
                    async for kind, event in _with_sse_heartbeat(
                        orch.run(
                            session.blackboard,
                            payload.input,
                            session_id=session_id,
                            session_log=session_log,
                            extra_tools=extra_tools,
                        ),
                        interval=10.0,
                    ):
                        if await request.is_disconnected():
                            break
                        if kind == "heartbeat":
                            yield ": keepalive\n\n"
                            continue
                        # 解包编排器事件 (ev_type, ev_data)
                        ev_type, ev_data = event
                        if ev_type == "reasoning":
                            text = ev_data.get("text", "")
                            reasoning_parts.append(text)
                            yield (
                                f"event: reasoning_step\n"
                                f"data: {json.dumps({'type': 'reasoning', 'text': text})}\n\n"
                            )
                        elif ev_type == "phase":
                            phase = ev_data.get("phase", 0)
                            name = ev_data.get("name", "")
                            agent_name = ev_data.get("agent", "")
                            yield (
                                f"event: reasoning_step\n"
                                f"data: {json.dumps({'type': 'pipeline_step', 'phase': phase, 'phase_name': name, 'agent': agent_name})}\n\n"
                            )
                        elif ev_type == "delta":
                            content = ev_data.get("content", "")
                            final_text += content
                            yield f"data: {json.dumps({'type': 'delta', 'content': content})}\n\n"
                        elif ev_type == "error":
                            err = ev_data.get("error", "编排执行失败")
                            yield f"data: {json.dumps({'type': 'error', 'error': err})}\n\n"
                        elif ev_type == "done":
                            content = ev_data.get("content", "")
                            _flush_to_session()
                            yield f"data: {json.dumps({'type': 'done', 'content': content or final_text})}\n\n"
                    _flush_to_session()
                    yield "data: [DONE]\n\n"
                    return
                except Exception as e:  # noqa: BLE001
                    logger.exception("对话自动编排处理失败: %s", e)
                    yield f"data: {json.dumps({'type': 'error', 'error': _format_detailed_error(e, '对话编排')})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

            try:
                # 会话 ID 已在克隆副本上绑定（沙箱审批 / 记忆 / 审计日志均可识别当前会话）
                # 流式对话（传入历史上下文 + 多模态 user 消息 + 附件工具 + 历史经验）
                # 包一层 SSE 心跳：工具执行等静默期定期发送注释行保活，防止连接超时断开
                async for kind, event in _with_sse_heartbeat(
                    agent.run_stream(
                        payload.input,
                        prior_history=prior_history,
                        extra_tools=extra_tools,
                        user_chat_message=user_chat_message,
                        experience_block=experience_block,
                    ),
                    interval=15.0,
                ):
                    if await request.is_disconnected():
                        # 前端断开（切换页面/刷新）：保留已产生内容
                        break
                    if kind == "heartbeat":
                        # SSE 注释行：不产生前端事件，仅维持 TCP 连接活跃
                        yield ": keepalive\n\n"
                        continue
                    if event.type == "reasoning":
                        reasoning_parts.append(event.content)
                        yield (
                            f"event: reasoning_step\n"
                            f"data: {json.dumps({'type': 'reasoning', 'text': event.content})}\n\n"
                        )
                    elif event.type == "stream_reset":
                        # 断流恢复：通知前端清空思考区与半截内容，从干净状态重新渲染
                        yield (
                            f"event: reasoning_step\n"
                            f"data: {json.dumps({'type': 'stream_reset'})}\n\n"
                        )
                    elif event.type == "text":
                        final_text += event.content
                        yield f"data: {json.dumps({'type': 'delta', 'content': event.content})}\n\n"
                    elif event.type == "tool_call":
                        tool_events.append(
                            {
                                "type": "function_call",
                                "name": event.tool_name,
                                "arguments": event.tool_args,
                            }
                        )
                        # 前端期望 reasoning_step 命名事件展示工具调用过程
                        yield (
                            f"event: reasoning_step\n"
                            f"data: {json.dumps({'type': 'tool_call', 'tool': event.tool_name, 'arguments': event.tool_args})}\n\n"
                        )
                    elif event.type == "tool_result":
                        tool_events.append(
                            {
                                "type": "function_call_output",
                                "id": event.tool_call_id,
                                "name": event.tool_name,
                                "output": event.tool_result,
                            }
                        )
                        yield (
                            f"event: reasoning_step\n"
                            f"data: {json.dumps({'type': 'tool_output', 'tool': event.tool_name, 'output': event.tool_result})}\n\n"
                        )
                    elif event.type == "sandbox_approval":
                        # 沙箱审批：转发为 user_prompt 事件，前端展示审批弹窗
                        sandbox_sid = event.sandbox_session_id or ""
                        prompt_id = f"sandbox:{sandbox_sid}:{event.tool_name}"
                        reason = event.sandbox_reason or "需要审批"
                        yield (
                            "event: user_prompt\n"
                            f"data: {json.dumps({'prompt_id': prompt_id, 'prompt_type': 'sandbox_approval', 'title': '工具执行审批', 'message': f'工具 `{event.tool_name}` 需要审批: {reason}', 'command': '', 'options': ['approve', 'deny'], 'is_password': False})}\n\n"
                        )
                    elif event.type == "done":
                        # 正常完成：保存完整内容；记忆已由 agent 在回合结束前自动学习
                        final_text = event.content or final_text
                        _flush_to_session()
                        # 评价闭环：本回合有结论 → 被注入的经验记为「有用」（提升后续排序）
                        if injected_exp_ids and (final_text or "").strip():
                            for _eid in injected_exp_ids:
                                try:
                                    app.state.memory_service.record_feedback(
                                        _eid, True, actor="agent",
                                        note="注入后本回合产出结论")
                                except Exception:  # noqa: BLE001
                                    pass
                        yield f"data: {json.dumps({'type': 'done', 'content': event.content})}\n\n"
            except ModelNotConfiguredError as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            except Exception as e:  # noqa: BLE001
                # 向客户端回显详细原因（异常类型/上下文/排查建议），便于运维快速定位；
                # 完整 traceback 仍记录到服务端日志
                logger.exception("对话请求处理失败: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'error': _format_detailed_error(e, '对话')})}\n\n"
            finally:
                # 关键：无论正常完成、连接断开（GeneratorExit/CancelledError）、还是异常，
                # 只要本轮产生了内容，就持久化，避免切换页面/刷新后对话丢失。
                _flush_to_session()
                try:
                    yield "data: [DONE]\n\n"
                except Exception:  # noqa: BLE001
                    pass

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------
    # 安全护栏管理（运行时规则，JSON 持久化，改动即时生效）
    # ------------------------------------------------------------------
    @app.get(
        "/api/v1/guardrails",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def get_guardrails() -> dict:
        store = GuardrailStore.get()
        return store.load().to_dict()

    @app.put(
        "/api/v1/guardrails",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def update_guardrails(payload: GuardrailConfigRequest) -> dict:
        ssrf_req = payload.settings.ssrf
        cfg = GuardrailConfig(
            settings=GuardrailSettings(
                input_enabled=payload.settings.input_enabled,
                output_enabled=payload.settings.output_enabled,
                max_input_length=payload.settings.max_input_length,
                ssrf=SSRFGuardrailSettings(
                    enabled=ssrf_req.enabled,
                    block_private=ssrf_req.block_private,
                    block_loopback=ssrf_req.block_loopback,
                    block_link_local=ssrf_req.block_link_local,
                    block_reserved=ssrf_req.block_reserved,
                    block_multicast=ssrf_req.block_multicast,
                    block_unspecified=ssrf_req.block_unspecified,
                    allowlist_cidrs=list(ssrf_req.allowlist_cidrs or []),
                    allowlist_hosts=list(ssrf_req.allowlist_hosts or []),
                ),
            ),
            rules=[GuardrailRule(**r.model_dump()) for r in payload.rules],
        )
        errors = validate_guardrail_config(cfg)
        if errors:
            raise HTTPException(status_code=400, detail="；".join(errors))
        saved = GuardrailStore.get().save(cfg)
        return saved.to_dict()

    @app.post(
        "/api/v1/guardrails/test",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def test_guardrail_endpoint(payload: GuardrailTestRequest) -> dict:
        text = payload.text or ""
        kind = payload.kind if payload.kind in ("input", "output") else "input"
        blocked, message, rule_id = test_guardrail(text, kind)
        return {"blocked": blocked, "message": message, "rule_id": rule_id}

    @app.post(
        "/api/v1/guardrails/reset",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def reset_guardrails() -> dict:
        store = GuardrailStore.get()
        return store.reset().to_dict()

    # ------------------------------------------------------------------
    # 沙箱策略配置（集成在安全护栏下）
    # ------------------------------------------------------------------
    @app.get(
        "/api/v1/guardrails/sandbox",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def get_sandbox_policy() -> dict:
        """获取沙箱策略配置。"""
        sandbox: Sandbox = app.state.sandbox
        policy = sandbox.policy
        return {
            "enabled": policy.enabled,
            "default_permission": policy.default_permission,
            "tool_permissions": policy.tool_permissions,
            "auto_approve_after": policy.auto_approve_after,
            "max_dangerous_per_turn": policy.max_dangerous_per_turn,
        }

    @app.put(
        "/api/v1/guardrails/sandbox",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def update_sandbox_policy(payload: SandboxPolicyRequest) -> dict:
        """更新沙箱策略配置。"""
        sandbox: Sandbox = app.state.sandbox
        sandbox.policy.enabled = payload.enabled
        sandbox.policy.default_permission = payload.default_permission
        sandbox.policy.tool_permissions = payload.tool_permissions
        sandbox.policy.auto_approve_after = payload.auto_approve_after
        sandbox.policy.max_dangerous_per_turn = payload.max_dangerous_per_turn
        return {
            "enabled": sandbox.policy.enabled,
            "default_permission": sandbox.policy.default_permission,
            "tool_permissions": sandbox.policy.tool_permissions,
            "auto_approve_after": sandbox.policy.auto_approve_after,
            "max_dangerous_per_turn": sandbox.policy.max_dangerous_per_turn,
        }

    # ==================================================================
    # 长期记忆：memory 子系统 REST（取代历史上所有经验引擎端点）
    # 全新实现 baize.memory：Episode / 经验 / 语义事实 / 时间知识图谱 /
    # 证据链 / 演进操作（REVISE/SUPERSEDE/INVALIDATE/Consolidation）
    # ==================================================================

    def _memory() -> MemoryService:
        """访问全局 memory 子系统（长期记忆门面）。"""
        return app.state.memory_service

    @app.get(
        "/api/v1/memory/stats",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_stats() -> dict:
        return _memory().stats()

    # ---- 经验（Experience） -------------------------------------------------
    @app.get(
        "/api/v1/memory/experiences",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_experiences(status: Optional[str] = None,
                           scope: Optional[str] = None,
                           include_superseded: bool = False) -> dict:
        rows = _memory().list_experiences(status=status, scope=scope,
                                          include_superseded=include_superseded)
        return {"total": len(rows), "experiences": rows}

    @app.get(
        "/api/v1/memory/experiences/{experience_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_experience_get(experience_id: str) -> dict:
        rec = _memory().get_experience(experience_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="经验不存在")
        return {"experience": rec}

    @app.post(
        "/api/v1/memory/experiences",
        response_model=dict,
        status_code=201,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_experience_create(payload: MemoryExperienceCreate) -> dict:
        rec = _memory().create_experience(
            title=payload.title, content=payload.content, tags=payload.tags,
            kind=payload.kind, scope=payload.scope, agent_key=payload.agent_key,
            source_session_id=payload.source_session_id,
            importance=payload.importance,
        )
        return {"experience": rec, "action": "created"}

    @app.put(
        "/api/v1/memory/experiences/{experience_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_experience_update(experience_id: str,
                                 payload: MemoryExperienceUpdate) -> dict:
        fields = payload.model_dump(exclude_none=True, exclude={"note"})
        rec = _memory().revise(experience_id, actor="user",
                               note=payload.note or "", fields=fields)
        if rec is None:
            raise HTTPException(status_code=404, detail="经验不存在")
        return {"experience": rec}

    @app.put(
        "/api/v1/memory/experiences/{experience_id}/status",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_experience_status(experience_id: str,
                                 payload: MemoryStatusRequest) -> dict:
        rec = _memory().set_status(experience_id, payload.status,
                                   actor="user", note=payload.note)
        if rec is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"experience": rec}

    @app.post(
        "/api/v1/memory/experiences/{experience_id}/feedback",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_experience_feedback(experience_id: str,
                                   payload: MemoryFeedbackRequest) -> dict:
        """对经验打分（评价闭环）：有用提升检索权重；连续无用自动降级为 draft。"""
        rec = _memory().record_feedback(experience_id, payload.useful,
                                        actor="user", note=payload.note)
        if rec is None:
            raise HTTPException(status_code=404, detail="经验不存在")
        return {"experience": rec}

    @app.delete(
        "/api/v1/memory/experiences/{experience_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_experience_delete(experience_id: str) -> dict:
        """记忆不可物理删除：软失效（INVALIDATE），保留谱系与审计。"""
        rec = _memory().set_status(experience_id, "invalidated",
                                   actor="user", note="user 软删除（INVALIDATE）")
        if rec is None:
            raise HTTPException(status_code=404, detail="经验不存在")
        return {"ok": True}

    @app.post(
        "/api/v1/memory/consolidate",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    async def memory_consolidate(payload: MemoryConsolidateRequest) -> dict:
        """把一组同主题经验合并为一条更泛化的新经验（默认 draft，需确认生效）。"""
        if len(payload.ids) < 2:
            raise HTTPException(status_code=400, detail="合并至少需要 2 条经验")
        try:
            out = await _memory().consolidate(
                payload.ids, actor="user", auto_commit=payload.auto_commit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("经验合并失败: %s", exc)
            raise HTTPException(status_code=400, detail=f"合并失败：{exc}")
        if out is None:
            raise HTTPException(status_code=400, detail="没有可合并的 active/draft 经验")
        return {"result": out}

    # ---- Episode（任务轨迹） -------------------------------------------------
    @app.get(
        "/api/v1/memory/episodes",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_episodes(limit: int = 100, session_id: str = "") -> dict:
        svc = _memory()
        rows = svc.list_episodes(limit=limit)
        if session_id:
            rows = [r for r in rows if r.get("session_id") == session_id]
        # 列表省去完整 steps（大块工具输出），仅保留概览
        slim = []
        for r in rows:
            item = dict(r)
            item["steps"] = len(item.get("steps", []))
            slim.append(item)
        return {"total": len(slim), "episodes": slim}

    @app.get(
        "/api/v1/memory/episodes/{episode_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_episode_get(episode_id: str) -> dict:
        ep = _memory().get_episode(episode_id)
        if ep is None:
            raise HTTPException(status_code=404, detail="Episode 不存在")
        return {"episode": ep}

    # ---- 混合检索 -----------------------------------------------------------
    @app.get(
        "/api/v1/memory/search",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_search(q: str = "", include_episodes: bool = False) -> dict:
        if not q.strip():
            return {"query": q, "experiences": [], "facts": [],
                    "entities": [], "neighbors": {"entities": [], "relations": []},
                    "episodes": []}
        return _memory().search(q.strip(), include_episodes=include_episodes)

    # ---- 语义事实 / 知识图谱 ------------------------------------------------
    @app.get(
        "/api/v1/memory/facts",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_facts(status: str = "active") -> dict:
        svc = _memory()
        rows = svc.list_facts(status=status or None)
        return {"total": len(rows), "facts": rows}

    @app.get(
        "/api/v1/memory/entities",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_entities(limit: int = 100, kind: str = "") -> dict:
        rows = _memory().list_entities(limit=max(1, min(limit, 2000)))
        if kind:
            rows = [e for e in rows if e.get("kind") == kind]
        return {"total": len(rows), "entities": rows}

    @app.get(
        "/api/v1/memory/graph",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def memory_graph() -> dict:
        """时间知识图谱 + 内容节点快照（供记忆可视化）。"""
        return _memory().graph_snapshot()

    # ------------------------------------------------------------------
    # 模型列表（单模型模式：返回当前配置的模型）
    # ------------------------------------------------------------------
    @app.get(
        "/api/v1/models",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def models_list() -> dict:
        m = app.state.model_config.load()
        models = []
        if m is not None:
            models.append(
                {
                    "name": m.model,
                    "provider": "configured",
                    "category": "Single",
                    "description": f"已配置模型 — {m.base_url}",
                }
            )
        return {"models": models}

    # ------------------------------------------------------------------
    # 编排管道
    # ------------------------------------------------------------------
    BUILTIN_PIPELINES = [
        {
            "id": "penetration_test",
            "name": "渗透测试流水线",
            "description": "标准渗透测试：侦察 → Web 渗透 → 红队利用",
            "steps": [
                {"agent_name": "recon_agent", "display_name": "侦察", "description": "信息收集"},
                {"agent_name": "web_pentester_agent", "display_name": "Web 渗透", "description": "漏洞检测"},
                {"agent_name": "redteam_agent", "display_name": "红队利用", "description": "漏洞利用"},
            ],
            "is_custom": False,
        },
    ]

    @app.get(
        "/api/v1/pipelines",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def pipelines_list() -> dict:
        return {"pipelines": BUILTIN_PIPELINES}

    @app.get(
        "/api/v1/pipelines/custom",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def custom_pipelines_list() -> dict:
        return {"pipelines": app.state.custom_pipelines.list()}

    @app.post(
        "/api/v1/pipelines/custom",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def custom_pipelines_create(payload: dict) -> dict:
        return app.state.custom_pipelines.create(dict(payload))

    @app.put(
        "/api/v1/pipelines/custom/{pipeline_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def custom_pipelines_update(pipeline_id: str, payload: dict) -> dict:
        pipeline = app.state.custom_pipelines.update(pipeline_id, dict(payload))
        if pipeline is None:
            raise HTTPException(status_code=404, detail="自定义管道不存在")
        return pipeline

    @app.delete(
        "/api/v1/pipelines/custom/{pipeline_id}",
        dependencies=[Depends(_require_api_key)],
    )
    def custom_pipelines_delete(pipeline_id: str) -> dict:
        ok = app.state.custom_pipelines.delete(pipeline_id)
        if not ok:
            raise HTTPException(status_code=404, detail="自定义管道不存在")
        return {"success": True}

    # ------------------------------------------------------------------
    # UX 辅助（标题/摘要）
    # ------------------------------------------------------------------
    @app.post(
        "/api/v1/ux/title",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    async def ux_title(payload: dict) -> dict:
        """根据对话内容生成简短标题。"""
        text = (payload.get("text") or payload.get("input") or "")[:80]
        title = text.strip().splitlines()[0][:30] if text.strip() else "新对话"
        return {"title": title}

    @app.post(
        "/api/v1/ux/summarize",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    async def ux_summarize(payload: dict) -> dict:
        """生成对话摘要。"""
        text = (payload.get("text") or payload.get("input") or "")
        summary = text[:120] + "…" if len(text) > 120 else text
        return {"summary": summary}

    # ------------------------------------------------------------------
    # 沙箱边界 API
    # ------------------------------------------------------------------
    @app.post(
        "/api/v1/sandbox/check",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def sandbox_check(payload: SandboxCheckRequest) -> dict:
        """检查工具是否允许执行。"""
        sandbox: Sandbox = app.state.sandbox
        return sandbox.check(payload.tool_name, payload.session_id)

    @app.post(
        "/api/v1/sandbox/approve",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def sandbox_approve(payload: SandboxApproveRequest) -> dict:
        """审批通过工具执行。"""
        sandbox: Sandbox = app.state.sandbox
        sandbox.approve(payload.tool_name, payload.session_id)
        return {"status": "ok", "tool": payload.tool_name}

    @app.post(
        "/api/v1/sandbox/deny",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def sandbox_deny(payload: SandboxDenyRequest) -> dict:
        """拒绝工具执行。"""
        sandbox: Sandbox = app.state.sandbox
        sandbox.deny(payload.tool_name, payload.session_id)
        return {"status": "ok", "tool": payload.tool_name, "denied": True}

    @app.get(
        "/api/v1/sandbox/stats/{session_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def sandbox_stats(session_id: str) -> dict:
        """获取会话沙箱统计。"""
        sandbox: Sandbox = app.state.sandbox
        return sandbox.get_session_stats(session_id)

    # ------------------------------------------------------------------
    # 认证
    # ------------------------------------------------------------------
    @app.post(
        "/api/v1/auth/login",
        response_model=AuthResponse,
    )
    def login(payload: LoginRequest) -> AuthResponse:
        auth: AuthManager = app.state.auth_manager
        token = auth.issue_token(payload.username, payload.password)
        if token is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        return AuthResponse(token=token, ok=True)

    return app


def _print_credentials(app: FastAPI, cfg) -> None:
    """在启动时向终端输出登录凭证。

    - 仅在生成了新凭证（首次启动或 BAIZE_AUTH_RESET_ON_BOOT=1）时打印；
    - 不再生成带 token 的登录 URL（token 不应进入 URL）；
    - 可通过 BAIZE_PRINT_CREDENTIALS=0 关闭输出。
    """
    if not app.state.require_auth:
        return
    if os.getenv("BAIZE_PRINT_CREDENTIALS", "1").lower() in ("0", "false", "no"):
        return
    auth: AuthManager = app.state.auth_manager
    username = auth.default_username
    separator = "=" * 44
    if auth.default_password and auth.default_token:
        lines = [
            f"\n{separator}",
            "  白泽·智脑 (Baize) 登录凭证（首次启动自动生成，请妥善保存）",
            f"  用户名:   {username}",
            f"  密码:     {auth.default_password}",
            f"  Token:    {auth.default_token}",
            f"  前端地址: {cfg.frontend_url or 'http://<host>:<port>/'}",
            "  使用方式: 登录页输入用户名/密码，或请求头携带 X-Baize-API-Key",
            "  (Token 请勿放入 URL，避免泄露到浏览器历史/日志)",
            f"{separator}\n",
        ]
    else:
        from baize.config import AUTH_DB_FILE

        lines = [
            f"\n{separator}",
            "  白泽·智脑 (Baize) 认证模式：沿用已存在的 admin 用户。",
            f"  用户名:   {username}",
            "  注意: 沿用模式下明文密码不可回溯（仅保存 PBKDF2 哈希），",
            "  若忘记原密码或需要新凭证，请执行以下任一方式重置：",
            f"    1) 删除认证文件 {AUTH_DB_FILE} 后重启；",
            "    2) 启动前设置环境变量 BAIZE_AUTH_RESET_ON_BOOT=1。",
            "  重置后将在此处打印新的用户名/密码/Token。",
            f"{separator}\n",
        ]
    sys.stdout.write("\n".join(lines))
    sys.stdout.flush()


def _discover_and_load_modules(app: FastAPI) -> None:
    """扫描 baize.modules entry point，加载所有已安装的扩展模块。

    每个 entry point 指向一个可调用对象 register(app: FastAPI) -> None，
    模块通过该函数向核心 app 注册额外路由和功能。

    同时扫描 baize.tools entry point，动态注册第三方工具插件。
    """
    try:
        eps = entry_points(group="baize.modules")
    except TypeError:
        # Python 3.10/3.11 兼容
        eps = entry_points().get("baize.modules", [])

    for ep in eps:
        try:
            mod = ep.load()
            if callable(mod):
                mod(app)
                app.state.loaded_modules[ep.name] = {
                    "installed": True,
                    "version": getattr(ep, "dist", None) and ep.dist.version or "unknown",
                }
                logger.info("已加载模块: %s", ep.name)
            else:
                logger.warning("模块 entry point %s 不是可调用对象，跳过", ep.name)
        except ModuleNotFoundError:
            logger.debug("模块 %s 未安装或缺少依赖，跳过", ep.name)
        except Exception:
            logger.exception("加载模块 %s 失败", ep.name)

    # 动态发现第三方工具插件（baize.tools entry point）
    try:
        from baize.tools import registry as _tool_registry

        _tool_registry.discover_entry_points()
    except Exception:  # noqa: BLE001
        logger.exception("发现工具插件失败")
