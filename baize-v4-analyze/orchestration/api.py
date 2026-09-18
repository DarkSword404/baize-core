"""
编排 API — 后台执行 + 运行管理。

端点设计：
    POST   /api/v1/runs                        创建并启动一次执行 (立即返回 run_id)
    GET    /api/v1/runs                        列出运行记录
    GET    /api/v1/runs/{run_id}               查询单次执行状态 + 事件列表
    GET    /api/v1/runs/{run_id}/stream         SSE 实时事件流（支持重连）
    POST   /api/v1/runs/{run_id}/confirm        人工确认恢复
    GET    /api/v1/pipelines/templates          模板列表
    POST   /api/v1/pipelines/{id}/parse         解析 YAML 为 PipelineDefinition
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request, Query, Depends, HTTPException

# Conditional import — avoid breaking if langgraph not installed
try:
    from sse_starlette.sse import EventSourceResponse
except ImportError:
    EventSourceResponse = None  # type: ignore

from baize.orchestration.node_types import (
    PipelineDefinition,
    PipelineNode,
    PipelineEdge,
    BranchRule,
    ParallelBranch,
)
from baize.orchestration.templates import get_builtin_templates
from baize.orchestration.run_store import get_run_store
from baize.orchestration.runner import get_runner
from baize.orchestration.run_store import get_activation_store
from baize.orchestration.instance_store import get_instance_store, DEFAULT_MAX_CONCURRENCY

logger = logging.getLogger(__name__)

# Pydantic models for request/response validation
try:
    from pydantic import BaseModel, Field
except ImportError:
    BaseModel = object  # type: ignore
    Field = None  # type: ignore


class PipelineRunRequest(BaseModel if BaseModel is not object else object):
    context: dict[str, Any] = {}
    webhook: str = ""
    dedup_key: str = ""  # 幂等去重键：同一事件重复提交时返回既有 run_id


class ConfirmRequest(BaseModel if BaseModel is not object else object):
    action: str  # "approve" | "reject" 或自定义选项值
    feedback: str = ""


def _build_pipeline_from_yaml(yaml_data: dict[str, Any]) -> PipelineDefinition:
    """从 YAML 字典构建 PipelineDefinition。

    兼容旧格式 steps 列表和新格式 nodes 图结构。
    """
    nodes_raw = yaml_data.get("nodes", yaml_data.get("steps", []))

    nodes: list[PipelineNode] = []
    for raw in nodes_raw:
        node_type = raw.get("type", "agent")

        branches: list[BranchRule] = []
        if node_type in ("decision", "condition", "ai_decision"):
            if node_type == "condition":
                node_type = "decision"
            for br in raw.get("branches", []):
                branches.append(BranchRule(
                    condition=br.get("when", br.get("condition", "")),
                    target=br.get("goto", br.get("target", "")),
                    label=br.get("label", ""),
                    is_default=br.get("default", False),
                ))

        parallel_branches: list[ParallelBranch] = []
        if node_type == "parallel":
            for pb in raw.get("branches", raw.get("parallel_branches", [])):
                if isinstance(pb, str):
                    parallel_branches.append(ParallelBranch(node_id=pb))
                elif isinstance(pb, dict):
                    sub_node = _node_from_dict(pb)
                    parallel_branches.append(ParallelBranch(
                        node_id=pb.get("id", ""),
                        node=sub_node,
                    ))

        confirm_options = raw.get("choices", raw.get("confirm_options", []))
        confirm_branches = raw.get("branches", raw.get("confirm_branches", {}))

        # 兼容旧 human 类型
        if node_type == "human":
            node_type = "confirm"

        try:
            timeout_seconds = int(raw.get("timeout_seconds", raw.get("timeout", 300)) or 300)
        except (TypeError, ValueError):
            timeout_seconds = 300
        try:
            max_retries = max(1, int(raw.get("max_retries", 1) or 1))
        except (TypeError, ValueError):
            max_retries = 1

        node = PipelineNode(
            id=raw.get("id", ""),
            type=node_type,
            display_name=raw.get("display_name", raw.get("name", raw.get("id", ""))),
            description=raw.get("description", ""),
            agent=raw.get("agent", ""),
            prompt_template=raw.get("prompt", raw.get("prompt_template", "")),
            branches=branches,
            parallel_branches=parallel_branches,
            merge_strategy=raw.get("merge_strategy", raw.get("merge", "all")),
            confirm_prompt=raw.get("confirm_prompt", raw.get("prompt", "")),
            confirm_options=confirm_options,
            confirm_branches=confirm_branches,
            # decision / ai_decision 专用
            decision_expression=raw.get("decision_expression", ""),
            decision_prompt=raw.get("decision_prompt", raw.get("ai_prompt", "")),
            decision_model=raw.get("decision_model", ""),
            target=raw.get("target", raw.get("goto", "")),
            save_dialog=bool(raw.get("save_dialog", False)),
            # 超时 / 重试
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            # 失败语义
            error_target=raw.get("error_target", raw.get("on_error", "")),
            ignore_error=bool(raw.get("ignore_error", False)),
        )
        nodes.append(node)

    # ---- 边（图编排连线）：旧模板无 edges，编译时回退顺序推断 ----
    edges: list[PipelineEdge] = []
    for e in yaml_data.get("edges", []):
        if isinstance(e, dict):
            edges.append(PipelineEdge(
                source=str(e.get("source", e.get("from", ""))),
                target=str(e.get("target", e.get("to", ""))),
                label=str(e.get("label", "")),
            ))

    pipe_type = yaml_data.get("type", "auto")
    # 如果存在 confirm/human 节点，自动检测为 manual
    has_confirm = any(n.type == "confirm" for n in nodes)
    if pipe_type == "auto" and has_confirm:
        pipe_type = "manual"

    return PipelineDefinition(
        id=yaml_data.get("id", ""),
        name=yaml_data.get("name", ""),
        type=pipe_type,
        description=yaml_data.get("description", ""),
        category=yaml_data.get("category", ""),
        tags=yaml_data.get("tags", []),
        nodes=nodes,
        edges=edges,
        triggers=yaml_data.get("triggers", []),
        context_schema=yaml_data.get("context_schema", {}),
        version=yaml_data.get("version", "1.0"),
        timeout_seconds=yaml_data.get("timeout_seconds", 3600),
        max_concurrency=yaml_data.get("max_concurrency", 1),
    )


def _node_from_dict(raw: dict[str, Any]) -> PipelineNode:
    """从单节点字典构建 PipelineNode（用于 parallel 子节点）。"""
    return PipelineNode(
        id=raw.get("id", ""),
        type=raw.get("type", "agent"),
        display_name=raw.get("display_name", raw.get("id", "")),
        agent=raw.get("agent", ""),
        prompt_template=raw.get("prompt", raw.get("prompt_template", "")),
    )


# ====================================================================
# Module Register
# ====================================================================

def _require_api_key(request: Request) -> None:
    """编排端点认证：与 baize-core 保持一致的 Token 校验。

    - 未开启认证（require_auth=False）时放行；
    - 认证管理器未初始化时放行；
    - 开启认证后，所有编排端点（含激活高危流水线）均需携带
      X-Baize-API-Key 或 Authorization: Bearer <token>。
    """
    if not getattr(request.app.state, "require_auth", False):
        return
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if auth_manager is None:
        return
    key = request.headers.get("X-Baize-API-Key") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")
    if not auth_manager.validate_token(key):
        raise HTTPException(status_code=401, detail="无效的 API 密钥")


def register(app: FastAPI) -> None:
    """注册编排 API 路由到 FastAPI 应用。"""
    runner = get_runner()
    store = get_run_store()

    # ---- 模板列表 ----
    @app.get("/api/v1/pipelines/templates", dependencies=[Depends(_require_api_key)])
    def pipeline_templates() -> dict:
        templates = get_builtin_templates()
        # 为模板添加 source 标记
        enriched = []
        for t in templates:
            t_copy = dict(t)
            t_copy["source"] = "builtin"
            enriched.append(t_copy)
        return {"templates": enriched}

    # ---- 删除内置模板 ----
    @app.delete("/api/v1/pipelines/templates/{template_id}", dependencies=[Depends(_require_api_key)])
    def delete_template(template_id: str) -> dict:
        from baize.api.custom_agents import get_deleted_store
        deleted = get_deleted_store()
        # 确认模板存在
        from baize.orchestration.templates import get_template_by_id as _find_tpl
        tpl = _find_tpl(template_id, skip_deleted=False)
        if tpl is None:
            return {"error": f"模板 '{template_id}' 未找到", "ok": False}
        deleted.delete_template(template_id)
        return {"ok": True, "template_id": template_id, "message": "模板已删除"}

    # ---- 恢复所有已删除模板 ----
    @app.post("/api/v1/pipelines/templates/reset", dependencies=[Depends(_require_api_key)])
    def reset_templates() -> dict:
        from baize.api.custom_agents import get_deleted_store
        deleted = get_deleted_store()
        count = deleted.reset_templates()
        return {"ok": True, "restored": count, "message": f"已恢复 {count} 个模板"}

    # ---- 统一模板列表（内置 + 用户自定义，供"模板→流水线"两级模型） ----
    @app.get("/api/v1/pipeline-templates", dependencies=[Depends(_require_api_key)])
    def list_pipeline_templates() -> dict:
        """模板库统一视图：内置模板(source=builtin) + 自定义模板(source=custom)。

        每个模板条目给出图定义（nodes/edges）与元信息，供创建流水线实例时快照。
        """
        templates = []
        for t in get_builtin_templates():
            t_copy = dict(t)
            t_copy["source"] = "builtin"
            templates.append(t_copy)
        try:
            from baize.api.custom_agents import CustomPipelineStore
            for p in CustomPipelineStore().list():
                p_copy = dict(p)
                p_copy.setdefault("source", "custom")
                templates.append(p_copy)
        except Exception:
            pass
        return {"templates": templates, "total": len(templates)}

    # ---- 流水线实例（由模板创建的可运行流水线） ----
    inst_store = get_instance_store()

    @app.get("/api/v1/pipelines/instances", dependencies=[Depends(_require_api_key)])
    def list_pipeline_instances() -> dict:
        """流水线实例列表（含状态/接收器绑定，不含图快照）。"""
        instances = []
        for inst in inst_store.list():
            data = inst.to_brief()
            tpl = _find_template_dict(inst.template_id)
            data["template_name"] = (tpl or {}).get("name", "")
            data["template_source"] = (tpl or {}).get("source", "")
            data["template_deleted"] = tpl is None
            instances.append(data)
        return {"instances": instances, "total": len(instances)}

    @app.post("/api/v1/pipelines/instances", dependencies=[Depends(_require_api_key)])
    async def create_pipeline_instance(request: Request) -> dict:
        """由模板创建流水线实例（模板图快照进实例，互不影响）。"""
        body = await request.json()
        template_id = str(body.get("template_id", "")).strip()
        tpl = _find_template_dict(template_id)
        if tpl is None:
            return {"ok": False, "error": f"流水线模板 '{template_id}' 未找到"}

        receiver_id = str(body.get("receiver_id", "") or "").strip()
        try:
            max_conc = int(body.get("max_concurrency") or DEFAULT_MAX_CONCURRENCY)
        except (TypeError, ValueError):
            max_conc = DEFAULT_MAX_CONCURRENCY
        inst = inst_store.create(
            name=str(body.get("name", "") or "").strip(),
            template=tpl,
            template_id=template_id,
            description=str(body.get("description", "") or "").strip(),
            receiver_id=receiver_id,
            max_concurrency=max_conc,
        )
        return {"ok": True, "instance": inst.to_dict()}

    @app.get("/api/v1/pipelines/instances/{instance_id}", dependencies=[Depends(_require_api_key)])
    def get_pipeline_instance(instance_id: str) -> dict:
        inst = inst_store.get(instance_id)
        if inst is None:
            return {"ok": False, "error": f"流水线实例 '{instance_id}' 未找到"}
        return {"ok": True, "instance": inst.to_dict(include_template=True)}

    @app.put("/api/v1/pipelines/instances/{instance_id}", dependencies=[Depends(_require_api_key)])
    async def update_pipeline_instance(instance_id: str, request: Request) -> dict:
        body = await request.json()
        inst = inst_store.get(instance_id)
        if inst is None:
            return {"ok": False, "error": f"流水线实例 '{instance_id}' 未找到"}
        if inst.enabled and ("receiver_id" in body or "max_concurrency" in body):
            return {
                "ok": False,
                "error": "流水线运行中（enabled）不能修改接收器/并发上限，请先停用",
            }
        updated = inst_store.update(
            instance_id,
            name=body.get("name"),
            description=body.get("description"),
            receiver_id=body.get("receiver_id"),
            max_concurrency=body.get("max_concurrency"),
        )
        if updated is None:
            return {"ok": False, "error": "更新失败"}
        return {"ok": True, "instance": updated.to_dict()}

    @app.delete("/api/v1/pipelines/instances/{instance_id}", dependencies=[Depends(_require_api_key)])
    def delete_pipeline_instance(instance_id: str) -> dict:
        inst = inst_store.get(instance_id)
        if inst is None:
            return {"ok": False, "error": f"流水线实例 '{instance_id}' 未找到"}
        if inst.enabled:
            return {
                "ok": False,
                "error": "流水线正在运行（enabled），请先停用再删除",
            }
        inst_store.delete(instance_id)
        # 清理持久化激活态（若曾直接激活）
        get_activation_store().deactivate(instance_id)
        from .session import get_session_manager
        get_session_manager().stop(instance_id)
        return {"ok": True, "instance_id": instance_id}

    @app.post("/api/v1/pipelines/instances/{instance_id}/enable", dependencies=[Depends(_require_api_key)])
    async def enable_pipeline_instance(instance_id: str) -> dict:
        """启用流水线实例：校验并激活 → 启动并行长驻会话。"""
        from .session import get_session_manager

        inst = inst_store.get(instance_id)
        if inst is None:
            return {"ok": False, "error": f"流水线实例 '{instance_id}' 未找到"}
        if inst.enabled:
            return {"ok": True, "instance_id": instance_id, "active": True, "message": "已在运行中"}

        definition = _build_instance_definition(inst)
        if definition is None:
            return {"ok": False, "error": "实例模板快照损坏，无法编译流水线"}
        if definition.type != "auto":
            return {
                "ok": False,
                "error": "该模板含人工介入节点（manual），不可常驻启用；请在对话中手动触发",
            }

        effective_receiver = inst.receiver_id or _receiver_node_agent(definition)
        if not effective_receiver:
            return {
                "ok": False,
                "error": "请先为该流水线绑定数据接收器（创建实例时选择 receiver_id）",
            }
        # 接收器存在性校验 + 冲突检查（同一接收器同时只允许一个启用实例消费）
        try:
            from baize.receivers.store import ReceiverStore
            cfg = ReceiverStore.get_instance().get(effective_receiver)
        except Exception:
            cfg = None
        if cfg is None:
            return {"ok": False, "error": f"接收器 '{effective_receiver}' 不存在，请先创建"}
        conflict = _receiver_conflict(inst_store, inst.id, effective_receiver)
        if conflict:
            return {
                "ok": False,
                "error": f"接收器 '{effective_receiver}' 已被流水线实例 '{conflict.name}' 占用，请先停用",
            }

        inst_store.update(instance_id, receiver_id=effective_receiver)
        inst = inst_store.set_enabled(instance_id, True)
        get_activation_store().activate(instance_id, receiver_id=effective_receiver)
        get_session_manager().ensure(definition)
        return {
            "ok": True,
            "instance_id": instance_id,
            "active": True,
            "receiver_id": effective_receiver,
            "max_concurrency": definition.max_concurrency,
        }

    @app.post("/api/v1/pipelines/instances/{instance_id}/disable", dependencies=[Depends(_require_api_key)])
    def disable_pipeline_instance(instance_id: str) -> dict:
        """停用流水线实例：停止并行长驻会话并去激活。"""
        from .session import get_session_manager

        inst = inst_store.get(instance_id)
        if inst is None:
            return {"ok": False, "error": f"流水线实例 '{instance_id}' 未找到"}
        get_session_manager().stop(instance_id)
        get_activation_store().deactivate(instance_id)
        inst_store.set_enabled(instance_id, False)
        return {"ok": True, "instance_id": instance_id, "active": False}

    @app.post("/api/v1/pipelines/instances/{instance_id}/sync", dependencies=[Depends(_require_api_key)])
    def sync_pipeline_instance(instance_id: str) -> dict:
        """把实例图快照同步到模板最新定义（实例绑定/接收器/并发上限不变）。"""
        inst = inst_store.get(instance_id)
        if inst is None:
            return {"ok": False, "error": f"流水线实例 '{instance_id}' 未找到"}
        if inst.enabled:
            return {"ok": False, "error": "流水线运行中，请先停用再同步模板"}
        tpl = _find_template_dict(inst.template_id)
        if tpl is None:
            return {"ok": False, "error": "模板已被删除，无法同步"}
        updated = inst_store.sync_from_template(instance_id, tpl)
        return {"ok": True, "instance": updated.to_dict()}

    @app.post("/api/v1/pipelines/instances/{instance_id}/test", dependencies=[Depends(_require_api_key)])
    async def test_pipeline_instance(instance_id: str, request: Request) -> dict:
        """实例测试投递：给该实例绑定的接收器投递一条测试入站数据。

        语义等同一次真实入站：数据进入持久收件箱(inbox)，由已启用的并行长驻
        会话 claim 后生成一次独立 run / 流水线对话。实例未启用时数据先排队，
        启用后会自动消费（与真实接收数据一致）。
        """
        inst = inst_store.get(instance_id)
        if inst is None:
            return {"ok": False, "error": f"流水线实例 '{instance_id}' 未找到"}
        definition = _build_instance_definition(inst)
        if definition is None:
            return {"ok": False, "error": "实例模板快照损坏，无法编译流水线"}

        body = await request.json()
        payload = body.get("payload", body.get("content", ""))
        if isinstance(payload, (dict, list)):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "json"
        else:
            raw = str(payload).encode("utf-8")
            content_type = "text"

        receiver_id = inst.receiver_id or _receiver_node_agent(definition)
        if not receiver_id:
            return {"ok": False, "error": "该流水线未绑定数据接收器，无法投递测试数据"}
        try:
            from baize.receivers.store import ReceiverStore
            cfg = ReceiverStore.get_instance().get(receiver_id)
        except Exception:
            cfg = None
        if cfg is None:
            return {
                "ok": False,
                "error": f"接收器 '{receiver_id}' 不存在，请先在「数据接收器」页创建",
            }

        from baize.receivers.inbox import get_alert_inbox
        seq, created = get_alert_inbox().enqueue(
            receiver_id=receiver_id,
            raw_payload=raw,
            content_type=content_type,
            source="instance_test",
            metadata={
                "test": True,
                "instance_id": instance_id,
                "nonce": str(uuid.uuid4()),
            },
        )
        return {
            "ok": True,
            "instance_id": instance_id,
            "receiver_id": receiver_id,
            "seq": int(seq),
            "created": bool(created),
            "enabled": bool(inst.enabled),
            "message": (
                "测试数据已进入接收器队列，运行中的并行会话将自动消费"
                if inst.enabled
                else "测试数据已进入接收器队列；流水线未启用，启用后会自动消费"
            ),
        }

    @app.get("/api/v1/pipelines/instances/{instance_id}/status", dependencies=[Depends(_require_api_key)])
    def pipeline_instance_status(instance_id: str) -> dict:
        """实例状态：启用标记 + 长驻会话运行态 + 收件箱积压 + 最近运行。"""
        from .session import get_session_manager

        inst = inst_store.get(instance_id)
        if inst is None:
            return {"ok": False, "error": f"流水线实例 '{instance_id}' 未找到"}
        info = {"instance_id": instance_id, "enabled": inst.enabled}
        info.update(get_activation_store().get_status(instance_id))
        try:
            info.update(get_session_manager().status(instance_id))
        except Exception:
            pass
        try:
            from baize.orchestration.run_store import get_run_store
            recent = get_run_store().list_runs(pipeline_id=instance_id, limit=5)
            info["recent_runs"] = recent
        except Exception:
            info["recent_runs"] = []
        return info

    # ---- YAML 解析/验证 ----
    @app.post("/api/v1/pipelines/{pipeline_id}/parse", dependencies=[Depends(_require_api_key)])
    async def pipeline_parse(pipeline_id: str, request: Request) -> dict:
        body = await request.json()
        try:
            pipeline = _build_pipeline_from_yaml(body)
            errors = pipeline.validate()
            return {
                "ok": len(errors) == 0,
                "pipeline": {
                    "id": pipeline.id,
                    "name": pipeline.name,
                    "type": pipeline.type,
                    "nodes": [
                        {
                            "id": n.id,
                            "type": n.type,
                            "display_name": n.display_name,
                        }
                        for n in pipeline.nodes
                    ],
                },
                "errors": errors,
            }
        except Exception as e:
            return {"ok": False, "errors": [str(e)]}

    # ---- 创建并启动执行 ----
    @app.post("/api/v1/runs", dependencies=[Depends(_require_api_key)])
    async def create_run(request: Request) -> dict:
        body = await request.json()
        pipeline_id = body.get("pipeline_id", "")

        # 查找管道定义：先查模板，再查自定义
        pipeline_def = _find_pipeline(pipeline_id)
        if pipeline_def is None:
            return {"error": f"流水线 '{pipeline_id}' 未找到", "ok": False}

        context = body.get("context", {})
        webhook = body.get("webhook", "")
        dedup_key = body.get("dedup_key", "")

        # 幂等去重：提交前先查是否已有同 key 的 run（避免把首次提交误判为 duplicate）
        existing = get_run_store().find_by_dedup_key(dedup_key) if dedup_key else None

        run_id = await runner.submit(pipeline_def, context, webhook, dedup_key)
        runner.cache_pipeline(pipeline_def)

        record = runner.get_run(run_id)
        return {
            "ok": True,
            "run_id": run_id,
            "pipeline_id": pipeline_id,
            "status": record.status if record else "running",
            "duplicate": bool(existing),
        }

    # ---- 列出运行记录 ----
    @app.get("/api/v1/runs", dependencies=[Depends(_require_api_key)])
    def list_runs(
        pipeline_id: str = Query(default=""),
        status: str = Query(default=""),
        limit: int = Query(default=50),
    ) -> dict:
        runs = runner.list_runs(
            pipeline_id=pipeline_id or None,
            status=status or None,
            limit=limit,
        )
        return {"runs": runs, "total": len(runs)}

    # ---- 查询单次执行 ----
    @app.get("/api/v1/runs/{run_id}", dependencies=[Depends(_require_api_key)])
    def get_run(run_id: str) -> dict:
        record = runner.get_run(run_id)
        if record is None:
            return {"error": "运行记录未找到", "ok": False}
        return {
            "ok": True,
            "run": record.to_dict(),
        }

    # ---- SSE 事件流 ----
    @app.get("/api/v1/runs/{run_id}/stream", dependencies=[Depends(_require_api_key)])
    async def stream_events(
        run_id: str,
        last_event_id: str = Query(default=""),
    ):
        if EventSourceResponse is None:
            from fastapi.responses import StreamingResponse

            async def _fallback():
                async for event in runner.subscribe_events(run_id, last_event_id):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            return StreamingResponse(_fallback(), media_type="text/event-stream")

        async def _event_generator():
            async for event in runner.subscribe_events(run_id, last_event_id):
                if event.get("event_id") == "done":
                    yield {"event": "done", "data": "[DONE]"}
                    return
                event_type = event.get("type", "log")
                yield {
                    "event": event_type,
                    "data": json.dumps(event, ensure_ascii=False),
                }

        return EventSourceResponse(_event_generator())

    # ---- 人工确认恢复 ----
    @app.post("/api/v1/runs/{run_id}/confirm", dependencies=[Depends(_require_api_key)])
    async def confirm_run(run_id: str, request: Request) -> dict:
        body = await request.json()
        action = body.get("action", "")

        if not action:
            return {"error": "缺少 action 字段", "ok": False}

        record = await runner.resume_after_confirm(run_id, action)
        if record is None:
            return {"error": "运行记录未找到或状态不是 paused", "ok": False}

        return {
            "ok": True,
            "run_id": run_id,
            "status": record.status,
            "message": f"已执行操作: {action}",
        }

    # ---- 自动化流水线激活控制 ----
    @app.post("/api/v1/pipelines/{pipeline_id}/activate", dependencies=[Depends(_require_api_key)])
    async def activate_pipeline(pipeline_id: str, request: Request) -> dict:
        """开启自动化流水线 → 启动并行长驻会话，开始静默逐条研判。

        - pipeline_id 为流水线实例 → 走实例 enable 流程（实例接收器绑定优先）；
        - 否则旧行为：模板直接激活（请求体 receiver_id 可显式绑定接收器）。
        """
        from .session import get_session_manager

        activation = get_activation_store()

        # 流水线实例快速路径（正式两级模型）
        inst = inst_store.get(pipeline_id)
        if inst is not None:
            try:
                _body: dict[str, Any] = await request.json()
            except Exception:
                _body = {}
            override_receiver = str(_body.get("receiver_id", "") or "").strip()
            if override_receiver and override_receiver != inst.receiver_id:
                inst_store.update(pipeline_id, receiver_id=override_receiver)
            return await enable_pipeline_instance(pipeline_id)

        pipeline_def = _find_pipeline(pipeline_id)
        if pipeline_def is None:
            return {"error": f"流水线 '{pipeline_id}' 未找到", "ok": False}
        if pipeline_def.type != "auto":
            return {"error": "仅自动化流水线可切换激活状态", "ok": False}

        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            pass
        receiver_id = str(body.get("receiver_id", "") or "").strip()

        # 接收器解析优先级：请求绑定 > receiver 节点 agent
        effective_receiver = receiver_id or _receiver_node_agent(pipeline_def)
        if not effective_receiver:
            return {
                "error": "请为流水线绑定数据接收器（请求体 receiver_id 或 receiver 节点 agent）",
                "ok": False,
            }
        if receiver_id:
            try:
                from baize.receivers.store import ReceiverStore
                cfg = ReceiverStore.get_instance().get(receiver_id)
            except Exception:
                cfg = None
            if cfg is None:
                return {"error": f"接收器 '{receiver_id}' 不存在", "ok": False}

        activation.activate(pipeline_id, receiver_id=effective_receiver)
        get_session_manager().ensure(pipeline_def)
        return {
            "ok": True,
            "pipeline_id": pipeline_id,
            "active": True,
            "receiver_id": effective_receiver,
        }

    @app.post("/api/v1/pipelines/{pipeline_id}/deactivate", dependencies=[Depends(_require_api_key)])
    async def deactivate_pipeline(pipeline_id: str) -> dict:
        """关闭自动化流水线并停止其长驻会话（实例 id 时同步 enabled 标记）。"""
        from .session import get_session_manager

        activation = get_activation_store()
        if inst_store.get(pipeline_id) is not None:
            return disable_pipeline_instance(pipeline_id)
        activation.deactivate(pipeline_id)
        get_session_manager().stop(pipeline_id)
        return {"ok": True, "pipeline_id": pipeline_id, "active": False}

    @app.get("/api/v1/pipelines/{pipeline_id}/status", dependencies=[Depends(_require_api_key)])
    def pipeline_status(pipeline_id: str) -> dict:
        """获取流水线激活状态与会话运行态（实例 id 时附加实例信息）。"""
        from .session import get_session_manager

        activation = get_activation_store()
        inst = inst_store.get(pipeline_id)
        if inst is not None:
            info: dict[str, Any] = {
                "instance_id": pipeline_id,
                "enabled": inst.enabled,
                "template_id": inst.template_id,
                "max_concurrency": inst.max_concurrency,
            }
        else:
            info = dict(activation.get_status(pipeline_id))
        try:
            info.update(get_session_manager().status(pipeline_id))
        except Exception:
            pass
        return info

    # ---- 告警自动研判：收件箱 / 会话 / 死信查询 ----
    @app.get("/api/v1/alert-triage/queue", dependencies=[Depends(_require_api_key)])
    def alert_triage_queue(
        receiver_id: str = Query(default=""),
    ) -> dict:
        """收件箱积压与状态统计。"""
        try:
            from baize.receivers.inbox import get_alert_inbox
            inbox = get_alert_inbox()
            stats = inbox.stats(receiver_id)
            recent = inbox.list_items(
                receiver_id=receiver_id, limit=10, with_payload=False
            )
            return {"ok": True, "stats": stats, "recent": recent}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/v1/alert-triage/alerts", dependencies=[Depends(_require_api_key)])
    def alert_triage_alerts(
        receiver_id: str = Query(default=""),
        status: str = Query(default=""),
        limit: int = Query(default=50),
        offset: int = Query(default=0),
    ) -> dict:
        """逐条告警处理结果查询（按收件箱条目；run 详情经 /api/v1/runs/{run_id}）。"""
        try:
            from baize.receivers.inbox import get_alert_inbox
            inbox = get_alert_inbox()
            items = inbox.list_items(
                receiver_id=receiver_id,
                status=status,
                limit=min(int(limit), 200),
                offset=max(int(offset), 0),
                with_payload=False,
            )
            return {"ok": True, "alerts": items, "total": len(items)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/api/v1/alert-triage/dlq/{seq}/replay", dependencies=[Depends(_require_api_key)])
    def alert_triage_replay(seq: int) -> dict:
        """死信/失败告警重放：以新指纹重新入队（避免与既有 run 去重键冲突）。"""
        try:
            from baize.receivers.inbox import get_alert_inbox
            inbox = get_alert_inbox()
            item = inbox.get(seq)
            if item is None:
                return {"ok": False, "error": f"收件箱条目 {seq} 不存在"}
            if item.get("status") in ("queued", "processing"):
                return {"ok": False, "error": f"条目 {seq} 正在排队/处理中，无需重放"}
            new_fp = f"{item.get('fingerprint', '')}:replay:{int(time.time())}"
            new_seq, created = inbox.enqueue(
                receiver_id=item.get("receiver_id", ""),
                raw_payload=item.get("raw_payload") or b"",
                content_type=item.get("content_type", ""),
                source=item.get("source", ""),
                metadata=item.get("metadata") or {},
                fingerprint=new_fp,
            )
            return {"ok": True, "seq": new_seq, "created": created, "source_seq": seq}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- 人工介入流水线列表（供对话选择） ----
    @app.get("/api/v1/pipelines/manual", dependencies=[Depends(_require_api_key)])
    def list_manual_pipelines() -> dict:
        """列出所有人工介入类型的流水线，供对话时选择。"""
        templates = get_builtin_templates()
        manual = []

        def _add_pipeline(pipe: dict):
            definition = _build_pipeline_from_yaml(pipe)
            if definition.type == "manual":
                manual.append({
                    "id": definition.id,
                    "name": definition.name,
                    "description": definition.description,
                    "category": definition.category,
                    "tags": definition.tags,
                    "nodes_count": len(definition.nodes),
                    "node_types": [n.type for n in definition.nodes],
                })

        for tpl in templates:
            _add_pipeline(tpl)

        # 也查自定义管道
        try:
            from baize.api.custom_agents import CustomPipelineStore
            store = CustomPipelineStore()
            for p in store.list():
                _add_pipeline(p)
        except Exception:
            pass

        return {"pipelines": manual, "total": len(manual)}

    # ---- 服务启动：预填充管道缓存 + 恢复中断的 run ----
    @app.on_event("startup")
    async def _recover_interrupted_runs() -> None:
        try:
            # 1. 预填充 pipeline 定义缓存（内置模板 + 自定义管道）
            for tpl in get_builtin_templates():
                try:
                    runner.cache_pipeline(_build_pipeline_from_yaml(tpl))
                except Exception:
                    continue
            try:
                from baize.api.custom_agents import CustomPipelineStore
                for p in CustomPipelineStore().list():
                    try:
                        runner.cache_pipeline(_build_pipeline_from_yaml(p))
                    except Exception:
                        continue
            except Exception:
                pass

            # 2. 恢复服务重启前中断的 run
            recovered = await runner.recover_interrupted()
            if recovered:
                logger.info(f"已恢复 {len(recovered)} 个中断的 run: {recovered}")

            # 3. 恢复长驻会话（持久化激活态 → SessionSupervisor 拉起）
            try:
                from .session import get_session_manager
                resumed = await get_session_manager().resume_all(_find_pipeline)
                if resumed:
                    logger.info(f"已恢复 {len(resumed)} 个长驻会话: {resumed}")
            except Exception as e:
                logger.warning(f"启动恢复长驻会话失败: {e}")
        except Exception as e:  # 恢复失败不应阻止服务启动
            logger.warning(f"启动恢复中断 run 失败: {e}")

    logger.info("Orchestration API registered (background runner + SSE stream)")


# ====================================================================
# Pipeline 查找辅助（模板 → 流水线实例 两级模型）
# ====================================================================

def _find_template_dict(template_id: str) -> dict[str, Any] | None:
    """按 id 查找模板原始字典：内置模板 > 用户自定义模板。

    内置模板遵循已删除黑名单；返回条目会补 source 标记。
    """
    if not template_id:
        return None
    for tpl in get_builtin_templates():
        if tpl.get("id") == template_id:
            out = dict(tpl)
            out["source"] = "builtin"
            return out
    try:
        from baize.api.custom_agents import CustomPipelineStore
        for p in CustomPipelineStore().list():
            if p.get("id") == template_id:
                out = dict(p)
                out.setdefault("source", "custom")
                return out
    except Exception:
        pass
    return None


def _receiver_node_agent(definition: PipelineDefinition) -> str:
    """取模板/实例中 receiver 节点配置的接收器 id。"""
    receiver_node = next(
        (n for n in definition.nodes if n.type == "receiver"), None
    )
    if receiver_node is None:
        return ""
    return receiver_node.agent or receiver_node.id or ""


def _receiver_conflict(
    inst_store: Any, self_id: str, receiver_id: str
) -> Any | None:
    """同一接收器只允许被一个『已启用』实例绑定，避免并行抢单/重复处理。"""
    if not receiver_id:
        return None
    for other in inst_store.list():
        if (
            other.id != self_id
            and other.enabled
            and other.receiver_id
            and other.receiver_id == receiver_id
        ):
            return other
    return None


def _build_instance_definition(
    instance: Any,
) -> PipelineDefinition | None:
    """由流水线实例（模板快照）构建可运行的 PipelineDefinition。

    关键：运行维度的 id = 实例 id（激活态/run/历史都挂到实例），
    max_concurrency = 实例并发上限（默认 10）。
    """
    snapshot = instance.template_snapshot or {}
    if not snapshot.get("nodes") and not snapshot.get("steps"):
        return None
    try:
        definition = _build_pipeline_from_yaml(snapshot)
    except Exception as e:
        logger.warning(f"实例 {instance.id} 模板快照解析失败: {e}")
        return None
    definition.id = instance.id
    if instance.name:
        definition.name = instance.name
    definition.max_concurrency = int(instance.max_concurrency or DEFAULT_MAX_CONCURRENCY)
    return definition


def _find_pipeline(pipeline_id: str) -> PipelineDefinition | None:
    """查找流水线定义：流水线实例 > 内置模板 > 自定义模板。

    - 传入 id 为流水线实例 id → 返回实例定义（id 被改写为实例 id，
      接收器由实例绑定决定，并发上限来自实例配置）；
    - 否则回退模板直接运行（旧行为：模板即运行对象）。
    """
    # 1. 流水线实例
    inst = get_instance_store().get(pipeline_id) if pipeline_id else None
    if inst is not None:
        definition = _build_instance_definition(inst)
        if definition is not None:
            return definition

    # 2. 内置模板
    for tpl in get_builtin_templates():
        if tpl.get("id") == pipeline_id:
            return _build_pipeline_from_yaml(tpl)

    # 3. 用户自定义模板
    try:
        from baize.api.custom_agents import CustomPipelineStore
        store = CustomPipelineStore()
        for p in store.list():
            if p.get("id") == pipeline_id:
                return _build_pipeline_from_yaml(p)
    except Exception:
        pass

    return None
