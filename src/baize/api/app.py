"""Baize Web API 主应用（独立实现）。

基于 FastAPI 提供浏览器/服务器架构下的核心能力：
- 健康检查、模型配置、智能体/工具列表
- 会话管理（创建、列表、删除）
- 流式对话
- 认证（启动生成凭证）
"""

from __future__ import annotations

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
from baize.config import ModelConfigStore, SingleModelConfig, get_server_config
from baize.multimodal import build_user_message
from baize.receivers.manager import ReceiverManager
from baize.receivers.webhook import handle_webhook
from baize.sdk.client import ModelNotConfiguredError


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


class ModelConfigResponse(BaseModel):
    base_url: str
    api_key: str
    model: str
    context_max_turns: int = 0
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


# ----------------------------------------------------------------------
# 认证依赖
# ----------------------------------------------------------------------
def _require_api_key(request: Request) -> None:
    auth_manager: AuthManager = request.app.state.auth_manager
    if not request.app.state.require_auth:
        return
    key = request.headers.get("X-Baize-API-Key") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not key:
        # 支持 URL 参数 token
        key = request.query_params.get("token", "")
    if not auth_manager.validate_token(key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或缺失 API Token",
        )


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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 全局状态
    app.state.session_manager = session_manager or SessionManager()
    app.state.model_config = ModelConfigStore()
    app.state.auth_manager = AuthManager()
    app.state.custom_agents = CustomAgentStore()
    app.state.custom_pipelines = CustomPipelineStore()
    app.state.attachment_store = AttachmentStore()
    app.state.require_auth = cfg.require_auth
    app.state.loaded_modules: dict[str, dict] = {}  # 已加载模块注册表

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
        m = app.state.model_config.save(
            SingleModelConfig(
                base_url=base_url,
                api_key=payload.api_key.strip(),
                model=model,
                context_max_turns=context_max_turns,
            )
        )
        return ModelConfigResponse(
            base_url=m.base_url,
            api_key=m.api_key,
            model=m.model,
            context_max_turns=int(m.context_max_turns or 0),
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
        return ToolsResponse(tools=list_tools())

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
        return {"ok": True}

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
        # 简化实现：将用户的交互响应作为普通消息追加。
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

        # 拼接历史（stateful 会话）：将会话已有消息作为上下文传给 Agent
        from baize.sdk.client import ChatMessage

        history_messages = app.state.session_manager.get_messages(session_id)
        prior_history = [
            ChatMessage(role=m["role"], content=m.get("content", ""))
            for m in history_messages
            if m.get("role") in ("user", "assistant") and not m.get("type")
        ]

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

        async def event_source():
            sm = app.state.session_manager
            # 本轮累积缓冲：思考过程、工具调用/结果记录、最终文本
            reasoning_parts: list[str] = []
            tool_events: list[dict] = []
            final_text = ""
            # 是否已把本轮内容持久化（正常完成 / 中断都只保存一次）
            saved = False

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

            try:
                # 流式对话（传入历史上下文 + 多模态 user 消息 + 附件工具）
                async for event in agent.run_stream(
                    payload.input,
                    prior_history=prior_history,
                    extra_tools=extra_tools,
                    user_chat_message=user_chat_message,
                ):
                    if await request.is_disconnected():
                        # 前端断开（切换页面/刷新）：保留已产生内容
                        break
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
                        yield f"data: {json.dumps({'type': 'done', 'content': event.content})}\n\n"
            except ModelNotConfiguredError as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            except Exception as e:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': f'服务器错误: {e}'})}\n\n"
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
    """在启动时向终端输出登录凭证。"""
    if not app.state.require_auth:
        return
    auth: AuthManager = app.state.auth_manager
    username = auth.default_username
    password = auth.default_password or ""
    token = auth.default_token or ""
    login_url = f"{cfg.frontend_url}/?token={token}"
    separator = "=" * 44
    lines = [
        f"\n{separator}",
        "  白泽 (Baize) 登录凭证（本次启动自动生成）",
        f"  用户名:   {username}",
        f"  密码:     {password}",
        f"  Token:    {token}",
        f"  登录 URL: {login_url}",
        "  (密码与 Token 每次重启自动重新生成)",
        f"{separator}\n",
    ]
    sys.stdout.write("\n".join(lines))
    sys.stdout.flush()


def _discover_and_load_modules(app: FastAPI) -> None:
    """扫描 baize.modules entry point，加载所有已安装的扩展模块。

    每个 entry point 指向一个可调用对象 register(app: FastAPI) -> None，
    模块通过该函数向核心 app 注册额外路由和功能。
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
