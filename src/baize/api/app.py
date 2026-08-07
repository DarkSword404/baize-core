"""Baize Web API 主应用（独立实现）。

基于 FastAPI 提供浏览器/服务器架构下的核心能力：
- 健康检查、模型配置、智能体/工具列表
- 会话管理（创建、列表、删除）
- 流式对话
- 认证（启动生成凭证）
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from baize import __version__
from baize.api.auth import AuthManager
from baize.api.custom_agents import CustomAgentStore, CustomPipelineStore
from baize.api.sessions import SessionManager
from baize.agents import list_agents, list_tools, get_agent
from baize.config import ModelConfigStore, SingleModelConfig, get_server_config
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
    app.state.require_auth = cfg.require_auth

    # ------------------------------------------------------------------
    # 启动凭证输出
    # ------------------------------------------------------------------
    _print_credentials(app, cfg)

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------
    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

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
    @app.get(
        "/api/v1/agents",
        response_model=AgentsResponse,
        dependencies=[Depends(_require_api_key)],
    )
    def agents_list() -> AgentsResponse:
        return AgentsResponse(agents=list_agents())

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
            agent = get_agent(None)  # 回退默认

        # 拼接历史（stateful 会话）：将会话已有消息作为上下文传给 Agent
        from baize.sdk.client import ChatMessage

        history_messages = app.state.session_manager.get_messages(session_id)
        prior_history = [
            ChatMessage(role=m["role"], content=m.get("content", ""))
            for m in history_messages
        ]

        async def event_source():
            try:
                # 流式对话（传入历史上下文）
                async for event in agent.run_stream(
                    payload.input, prior_history=prior_history
                ):
                    if await request.is_disconnected():
                        break
                    if event.type == "reasoning":
                        # 模型的实时思考过程
                        yield (
                            f"event: reasoning_step\n"
                            f"data: {json.dumps({'type': 'reasoning', 'text': event.content})}\n\n"
                        )
                    elif event.type == "text":
                        yield f"data: {json.dumps({'type': 'delta', 'content': event.content})}\n\n"
                    elif event.type == "tool_call":
                        # 前端期望 reasoning_step 命名事件展示工具调用过程
                        yield (
                            f"event: reasoning_step\n"
                            f"data: {json.dumps({'type': 'tool_call', 'tool': event.tool_name, 'arguments': event.tool_args})}\n\n"
                        )
                    elif event.type == "tool_result":
                        yield (
                            f"event: reasoning_step\n"
                            f"data: {json.dumps({'type': 'tool_output', 'tool': event.tool_name, 'output': event.tool_result})}\n\n"
                        )
                    elif event.type == "done":
                        # 保存到会话
                        app.state.session_manager.append_message(
                            session_id, "user", payload.input
                        )
                        app.state.session_manager.append_message(
                            session_id, "assistant", event.content
                        )
                        yield f"data: {json.dumps({'type': 'done', 'content': event.content})}\n\n"
                yield "data: [DONE]\n\n"
            except ModelNotConfiguredError as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': f'服务器错误: {e}'})}\n\n"
                yield "data: [DONE]\n\n"

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
    # 自定义智能体 CRUD
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
