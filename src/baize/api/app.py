"""Baize Web API 主应用（独立实现）。

基于 FastAPI 提供浏览器/服务器架构下的核心能力：
- 健康检查、模型配置、智能体/工具列表
- 会话管理（创建、列表、删除）
- 流式对话
- 认证（启动生成凭证）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from importlib.metadata import entry_points
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
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
    check_input_guardrail,
    test_guardrail,
    validate_guardrail_config,
)
from baize.config import ModelConfigStore, SingleModelConfig, get_server_config
from baize.multimodal import build_user_message
from baize.receivers.manager import ReceiverManager
from baize.receivers.webhook import handle_webhook
from baize.sdk.client import LLMClient, ModelNotConfiguredError, ChatMessage
from baize.tools.custom_tools import test_custom_tool
from baize.experiences import (
    GLOBAL_SCOPE,
    EmbeddingConfig,
    EmbeddingConfigStore,
    ExperienceRetriever,
    ExperienceStore,
    detect_turn_signals,
    refine_experience,
    resolve_embedding,
)


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


class MessageRequest(BaseModel):
    input: str
    agent: Optional[str] = None
    # 本次消息附带的附件 file_id 列表（已上传到会话的附件）
    attachments: list[str] = Field(default_factory=list)


class ExperienceRequest(BaseModel):
    title: str
    content: str
    scope: str = GLOBAL_SCOPE  # "global" | "agent:{agent_key}"
    tags: list[str] = Field(default_factory=list)
    source_session_id: str = ""
    source_agent: str = ""
    enabled: bool = True
    importance: int = 0


class ExperienceUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    scope: Optional[str] = None
    tags: Optional[list[str]] = None
    enabled: Optional[bool] = None
    importance: Optional[int] = None


class RefineRequest(BaseModel):
    session_id: str
    agent: str
    scope: str = "auto"  # "global" | "agent:{key}" | "auto"


class EmbeddingConfigRequest(BaseModel):
    provider: str = "none"  # none | openai | local
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    dimensions: int = 0


class AuthResponse(BaseModel):
    token: str | None = None
    ok: bool


class LoginRequest(BaseModel):
    username: str
    password: str


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


class GuardrailSettingsRequest(BaseModel):
    input_enabled: bool = True
    output_enabled: bool = False
    max_input_length: int = 16384


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
# 应用工厂
# ----------------------------------------------------------------------
def create_baize_api_app(
    *,
    session_manager: SessionManager | None = None,
) -> FastAPI:
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
    from baize.tools.custom_tools import CustomToolStore
    app.state.custom_tools = CustomToolStore()
    app.state.custom_tools.register_all()  # 启动时热注册已有自定义工具
    app.state.attachment_store = AttachmentStore()
    app.state.require_auth = cfg.require_auth
    app.state.loaded_modules: dict[str, dict] = {}  # 已加载模块注册表
    # 长期记忆：经验库 + 可插拔 embedding Provider + 检索器
    app.state.experience_store = ExperienceStore()
    app.state.embedding_config_store = EmbeddingConfigStore()
    app.state.embedding = resolve_embedding()
    app.state.experience_retriever = ExperienceRetriever(
        app.state.experience_store, app.state.embedding
    )

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
        # 统计该会话衍生的经验条目数（经验是长期资产，不随会话删除，仅供前端提示）
        derived = app.state.experience_store.get_by_source_session(session_id)
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

        # ── pattern 流水线会话：会话绑定了流水线时，走流水线事件源 ──
        pipeline_def = None
        if getattr(session, "pattern", None):
            try:
                from baize.orchestration.api import _find_pipeline
                pipeline_def = _find_pipeline(session.pattern)
            except Exception:  # orchestration 未安装或查找失败 → 回退默认 agent
                pipeline_def = None

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
        # 多模态 user 消息（图片注入 content_parts，其它注入附件提示）
        user_chat_message = build_user_message(
            payload.input,
            active_attachments,
            attachment_store=attachment_store,
            session_id=session_id,
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

        # ── 长期记忆：检索相关历史经验并注入 agent 上下文 ──
        experience_block = ""
        exp_agent_key = getattr(agent, "name", None) or agent_name or "default"
        try:
            retrieved = await app.state.experience_retriever.search(
                payload.input, exp_agent_key, top_k=3
            )
            if retrieved:
                experience_block = app.state.experience_retriever.build_block(retrieved)
                for item in retrieved:
                    app.state.experience_store.increment_hit(item.id)
        except Exception:  # noqa: BLE001
            logger.warning("经验检索失败", exc_info=True)

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
                    run_id = await runner.submit(
                        pipeline_def,
                        {
                            "text": payload.input,
                            "input": payload.input,
                            "message": payload.input,
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
                            output = edata.get("data", {}) or {}
                            text = ""
                            if isinstance(output, dict):
                                text = (
                                    output.get("report")
                                    or output.get("text")
                                    or output.get("output")
                                    or output.get("final_output")
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
                            # compiler 推送 data={"run_id","data":final_values}；runner 再推送 data={"report":...}
                            report = (
                                edata.get("report")
                                or (edata.get("data") or {}).get("report")
                                or (edata.get("data") or {}).get("final_output")
                                or ""
                            )
                            if isinstance(report, str) and report.strip():
                                final_text += report
                                yield f"data: {json.dumps({'type': 'delta', 'content': report})}\n\n"
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
                    yield f"data: {json.dumps({'type': 'error', 'error': '流水线执行失败，请查看服务端日志'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

            try:
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
                    elif event.type == "done":
                        # 正常完成：保存完整内容
                        final_text = event.content or final_text
                        _flush_to_session()
                        # 长期记忆：纯规则信号检测（不调 LLM），命中则提示前端可提炼经验
                        try:
                            prior_turns_text = " ".join(
                                f"{m.get('role')}: {m.get('content', '')}"
                                for m in history_messages
                            )
                            signals = detect_turn_signals(
                                tool_events, final_text, prior_turns_text, payload.input
                            )
                            if signals["should_refine"]:
                                yield (
                                    "event: experience_signal\n"
                                    f"data: {json.dumps({'type': 'experience_signal', 'reasons': signals['reasons'], 'session_id': session_id, 'agent': exp_agent_key})}\n\n"
                                )
                        except Exception:  # noqa: BLE001
                            logger.warning("经验信号检测失败", exc_info=True)
                        yield f"data: {json.dumps({'type': 'done', 'content': event.content})}\n\n"
            except ModelNotConfiguredError as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            except Exception as e:  # noqa: BLE001
                # 不向客户端回显内部异常细节，仅记录到服务端日志
                logger.exception("对话请求处理失败: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'error': '服务器内部错误，请查看服务端日志'})}\n\n"
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
        cfg = GuardrailConfig(
            settings=GuardrailSettings(
                input_enabled=payload.settings.input_enabled,
                output_enabled=payload.settings.output_enabled,
                max_input_length=payload.settings.max_input_length,
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
    # 长期记忆：经验库管理（CRUD + 提炼 + embedding 配置）
    # ------------------------------------------------------------------
    @app.get(
        "/api/v1/experiences",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def experiences_list(
        scope: Optional[str] = None,
        agent: Optional[str] = None,
        include_disabled: bool = True,
    ) -> dict:
        items = app.state.experience_store.list_items(
            scope=scope, agent_key=agent, include_disabled=include_disabled
        )
        return {"experiences": [i.to_dict() for i in items]}

    @app.get(
        "/api/v1/experiences/embedding-config",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def embedding_config_get() -> dict:
        cfg = app.state.embedding_config_store.load()
        data = {
            "provider": cfg.provider,
            "base_url": cfg.base_url,
            "api_key": cfg.api_key or "",
            "model": cfg.model,
            "dimensions": cfg.dimensions,
        }
        return {"config": data}

    @app.put(
        "/api/v1/experiences/embedding-config",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def embedding_config_put(payload: EmbeddingConfigRequest) -> dict:
        cfg = EmbeddingConfig(
            provider=payload.provider,
            base_url=payload.base_url.strip(),
            api_key=payload.api_key.strip(),
            model=payload.model.strip(),
            dimensions=payload.dimensions,
        )
        if cfg.provider not in ("none", "openai", "local"):
            raise HTTPException(status_code=400, detail="provider 必须是 none/openai/local")
        app.state.embedding_config_store.save(cfg)
        # 重建 Provider 与检索器，下次检索立即生效
        app.state.embedding = resolve_embedding(cfg)
        app.state.experience_retriever = ExperienceRetriever(
            app.state.experience_store, app.state.embedding
        )
        return {"ok": True, "config": {
            "provider": cfg.provider,
            "base_url": cfg.base_url,
            "api_key": cfg.api_key or "",
            "model": cfg.model,
            "dimensions": cfg.dimensions,
        }}

    @app.post(
        "/api/v1/experiences/reindex",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    async def experiences_reindex() -> dict:
        """为缺失/过期向量的经验条目批量补齐 embedding（配置向量 Provider 后调用）。"""
        embedding = app.state.embedding
        if not embedding.is_available():
            raise HTTPException(
                status_code=400,
                detail="未配置可用的向量 Provider，请先在设置中配置 embedding（openai/local）",
            )
        model = embedding.model_name
        store = app.state.experience_store
        missing = store.items_missing_embedding(model)
        if not missing:
            return {"ok": True, "indexed": 0, "total": 0}
        texts = [f"{i.title}\n{i.content}\n{' '.join(i.tags)}" for i in missing]
        try:
            vectors = await embedding.embed(texts)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=f"向量生成失败：{type(exc).__name__}: {exc}",
            ) from exc
        for item, vec in zip(missing, vectors):
            if vec:
                store.set_embedding(item.id, vec, model)
        return {"ok": True, "indexed": len(missing), "total": len(missing)}

    @app.get(
        "/api/v1/experiences/{experience_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def experiences_get(experience_id: str) -> dict:
        item = app.state.experience_store.get_item(experience_id)
        if item is None:
            raise HTTPException(status_code=404, detail="经验不存在")
        return {"experience": item.to_dict()}

    @app.post(
        "/api/v1/experiences",
        response_model=dict,
        status_code=201,
        dependencies=[Depends(_require_api_key)],
    )
    async def experiences_create(payload: ExperienceRequest) -> dict:
        item = app.state.experience_store.create(payload.model_dump())
        # 配置了向量 Provider 时自动生成 embedding，避免依赖手动 reindex
        embedding = app.state.embedding
        if embedding.is_available():
            try:
                text = f"{item.title}\n{item.content}\n{' '.join(item.tags)}"
                vecs = await embedding.embed([text])
                if vecs and vecs[0]:
                    app.state.experience_store.set_embedding(
                        item.id, vecs[0], embedding.model_name
                    )
                    refreshed = app.state.experience_store.get_item(item.id)
                    if refreshed is not None:
                        item = refreshed
            except Exception:  # noqa: BLE001
                logger.warning("经验向量自动生成失败", exc_info=True)
        return {"experience": item.to_dict()}

    @app.put(
        "/api/v1/experiences/{experience_id}",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    def experiences_update(experience_id: str, payload: ExperienceUpdateRequest) -> dict:
        item = app.state.experience_store.update(
            experience_id, payload.model_dump(exclude_none=True)
        )
        if item is None:
            raise HTTPException(status_code=404, detail="经验不存在")
        return {"experience": item.to_dict()}

    @app.delete(
        "/api/v1/experiences/{experience_id}",
        dependencies=[Depends(_require_api_key)],
    )
    def experiences_delete(experience_id: str) -> dict:
        ok = app.state.experience_store.delete(experience_id)
        if not ok:
            raise HTTPException(status_code=404, detail="经验不存在")
        return {"ok": True}

    @app.post(
        "/api/v1/sessions/{session_id}/experience/refine",
        response_model=dict,
        dependencies=[Depends(_require_api_key)],
    )
    async def session_experience_refine(
        session_id: str, payload: RefineRequest
    ) -> dict:
        """对整段会话（或最近几轮）做 LLM 复盘提炼，返回候选条目（不入库）。"""
        session = app.state.session_manager.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        messages = app.state.session_manager.get_messages(session_id)
        agent_key = payload.agent or getattr(session, "agent", "") or "default"

        # 收集最近几轮：user / assistant 正文 + 工具调用/结果
        user_msgs = [m for m in messages if m.get("role") == "user" and not m.get("type")]
        if not user_msgs:
            raise HTTPException(status_code=400, detail="会话没有可提炼的对话")
        last_user = user_msgs[-1]
        last_user_text = str(last_user.get("content", ""))
        tool_events: list[dict] = []
        final_text = ""
        # 最近一轮的 assistant 结论与工具轨迹
        for m in messages[-40:]:
            extra = m.get("extra") or {}
            if m.get("role") == "intermediate" and extra.get("type") == "function_call":
                tool_events.append(
                    {
                        "type": "function_call",
                        "name": extra.get("name", ""),
                        "arguments": extra.get("arguments", ""),
                    }
                )
            elif m.get("role") == "intermediate" and extra.get("type") == "function_call_output":
                tool_events.append(
                    {
                        "type": "function_call_output",
                        "name": extra.get("name", ""),
                        "output": extra.get("output", ""),
                    }
                )
            elif m.get("role") == "assistant" and m.get("content"):
                final_text = str(m.get("content", ""))

        client = LLMClient()
        candidate = await refine_experience(
            client,
            agent_key=agent_key,
            session_id=session_id,
            user_message=last_user_text,
            final_text=final_text,
            tool_events=tool_events,
            prior_history=messages,
            scope=payload.scope,
        )
        return {"candidate": candidate, "session_id": session_id, "agent": agent_key}

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
            "  白泽·智脑 (Baize) 登录凭证沿用首次启动时生成的密码/Token。",
            f"  用户名:   {username}",
            f"  如需重置，删除认证文件 {AUTH_DB_FILE} 后重启，",
            "  或设置环境变量 BAIZE_AUTH_RESET_ON_BOOT=1。",
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
