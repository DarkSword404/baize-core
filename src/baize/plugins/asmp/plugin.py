"""ASMP Harness plugin wrapper."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from baize.plugins.base import BasePlugin, PluginContext, PluginEvent, PluginManifest
from baize.sdk.agent import AgentTool

from .service import ASMPService


class ASMPPlugin(BasePlugin):
    manifest = PluginManifest(
        id="baize.asmp",
        name="Attack Situational Awareness Modeling Plugin",
        version="1.0.0",
        kind="state",
        description=(
            "动态攻击态势建模：节点、关系、证据、威胁假设、未知项与探索任务。"
            "只输出模型与建议，不执行工具，不替代 Harness 调度。"
        ),
        permissions=("session.read", "session.write", "tool.register", "ui.panel"),
        config_schema={"brief_max_chars": 5000},
    )

    def __init__(self) -> None:
        self.service: ASMPService | None = None

    async def start(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.service = ASMPService(Path(ctx.data_dir) / "asmp")
        try:
            ctx.services.register("asmp_service", self.service)
            ctx.services.register("asmp_plugin", self)
        except Exception:  # noqa: BLE001
            pass

    async def stop(self) -> None:
        if self.service is not None:
            self.service.close()

    def on_event(self, event: PluginEvent) -> None:
        if self.service is not None:
            self.service.on_event(event)

    def tools(self, session_id: str) -> list[AgentTool]:
        if self.service is None:
            return []
        svc = self.service

        def _json(value: Any, limit: int = 10000) -> str:
            return json.dumps(value, ensure_ascii=False, indent=2)[:limit]

        def snapshot() -> str:
            """获取当前攻击态势模型快照（节点/关系/证据/假设/未知项/任务）。"""
            return _json(svc.get_model_snapshot(session_id), 14000)

        def query(entity: str = "nodes", type_id: str = "", status: str = "",
                  category: str = "", limit: int = 30) -> str:
            """查询模型对象；entity=nodes/relations/hypotheses/unknowns/tasks。"""
            entity = str(entity or "nodes")
            if entity == "nodes":
                return _json(svc.query_nodes(session_id, type_id=type_id, status=status))
            if entity == "relations":
                return _json(svc.query_relations(session_id, type_id=type_id))
            if entity == "hypotheses":
                return _json(svc.get_hypotheses(session_id, status=status, category=category))
            if entity == "unknowns":
                return _json(svc.get_unknowns(session_id))
            if entity == "tasks":
                return _json(svc.propose_tasks(session_id, limit=limit))
            return _json({"error": f"unknown entity: {entity}"})

        def ingest(observation: dict[str, Any]) -> str:
            """提交一个标准化观察对象，更新模型并返回变更。"""
            if not isinstance(observation, dict):
                return "observation 必须是 JSON 对象"
            return _json(svc.ingest_observation(session_id, observation), 12000)

        def propose_tasks(limit: int = 10, include_paths: bool = True) -> str:
            """根据当前假设/未知项生成结构化探索任务建议。"""
            return _json(svc.propose_tasks(session_id, limit=limit), 12000)

        def update_hypothesis(hypothesis_id: str, status: str,
                              evidence_id: str = "", contradicting_evidence_id: str = "",
                              note: str = "", confidence: float = -1) -> str:
            """更新威胁假设状态；confirmed 必须有支持证据。"""
            conf = None if confidence < 0 else confidence
            return _json(svc.update_hypothesis(
                session_id, hypothesis_id, status=status,
                evidence_id=evidence_id,
                contradicting_evidence_id=contradicting_evidence_id,
                note=note, confidence=conf,
            ))

        def update_unknown(unknown_id: str, status: str = "resolved",
                           note: str = "") -> str:
            """更新未知项状态（open/resolved/accepted_risk/not_applicable/closed）。"""
            return _json(svc.update_unknown(session_id, unknown_id, status=status, note=note))

        def apply_result(task_id: str, status: str = "completed",
                         note: str = "", observation: dict[str, Any] | None = None) -> str:
            """将 Harness 的执行结果回写到任务/模型。"""
            return _json(svc.apply_execution_result(
                session_id, task_id, status=status, note=note, observation=observation,
            ))

        def propose_type(definition: dict[str, Any], reason: str = "") -> str:
            """提交新的节点/关系类型提案；不会自动替代已有类型。"""
            return _json(svc.propose_type_definition(session_id, definition, reason=reason))

        def validate_type(definition: dict[str, Any]) -> str:
            """校验节点/关系类型定义。"""
            return _json(svc.validate_type_definition(session_id, definition))

        def changes(limit: int = 100) -> str:
            """查看模型变更历史。"""
            return _json(svc.get_change_history(session_id, limit=limit), 12000)

        return [
            AgentTool(
                name="asmp_snapshot",
                description=(
                    "读取攻击态势模型快照：环境节点、关系、证据、未确认威胁假设、"
                    "未知项和探索任务。它是模型状态，不是漏洞结论。"
                ),
                parameters={"type": "object", "properties": {}, "required": []},
                handler=snapshot,
            ),
            AgentTool(
                name="asmp_query",
                description=(
                    "查询态势模型对象。entity=nodes/relations/hypotheses/unknowns/tasks；"
                    "可按 type_id/status/category 过滤。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "description": "nodes/relations/hypotheses/unknowns/tasks"},
                        "type_id": {"type": "string"},
                        "status": {"type": "string"},
                        "category": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                },
                handler=query,
            ),
            AgentTool(
                name="asmp_ingest",
                description=(
                    "提交标准化观察对象，触发节点/关系/证据增量更新与类型化分析。"
                    "观察必须包含 source/content 等字段；不要提交未经验证的 LLM 结论。"
                ),
                parameters={
                    "type": "object",
                    "properties": {"observation": {"type": "object"}},
                    "required": ["observation"],
                },
                handler=ingest,
            ),
            AgentTool(
                name="asmp_propose_tasks",
                description=(
                    "根据当前威胁假设、未知项和关系缺口生成探索任务建议。"
                    "任务只是建议，执行仍由 Harness 授权与调度。"
                ),
                parameters={
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                },
                handler=propose_tasks,
            ),
            AgentTool(
                name="asmp_update_hypothesis",
                description=(
                    "更新威胁假设状态（candidate/ready/in_progress/confirmed/refuted/"
                    "blocked/inconclusive/closed）。confirmed 必须有支持证据。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "hypothesis_id": {"type": "string"},
                        "status": {"type": "string"},
                        "evidence_id": {"type": "string"},
                        "contradicting_evidence_id": {"type": "string"},
                        "note": {"type": "string"},
                        "confidence": {"type": "number", "minimum": -1, "maximum": 1},
                    },
                    "required": ["hypothesis_id", "status"],
                },
                handler=update_hypothesis,
            ),
            AgentTool(
                name="asmp_update_unknown",
                description="更新未知项状态（open/resolved/accepted_risk/not_applicable/closed）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "unknown_id": {"type": "string"},
                        "status": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["unknown_id"],
                },
                handler=update_unknown,
            ),
            AgentTool(
                name="asmp_apply_result",
                description="把任务执行结果回写到态势模型（状态、说明、可选观察）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "status": {"type": "string"},
                        "note": {"type": "string"},
                        "observation": {"type": "object"},
                    },
                    "required": ["task_id"],
                },
                handler=apply_result,
            ),
            AgentTool(
                name="asmp_propose_type",
                description="提出新的节点/关系类型；只登记为 proposed，需校验后注册。",
                parameters={
                    "type": "object",
                    "properties": {
                        "definition": {"type": "object"},
                        "reason": {"type": "string"},
                    },
                    "required": ["definition"],
                },
                handler=propose_type,
            ),
            AgentTool(
                name="asmp_validate_type",
                description="校验节点/关系类型定义，返回结构错误与冲突。",
                parameters={
                    "type": "object",
                    "properties": {"definition": {"type": "object"}},
                    "required": ["definition"],
                },
                handler=validate_type,
            ),
            AgentTool(
                name="asmp_changes",
                description="查看模型变更历史，追溯节点/关系/假设的变化来源。",
                parameters={
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
                },
                handler=changes,
            ),
        ]

    def context_hint(self, session_id: str) -> str:
        return (
            "攻击态势建模插件已启用：可用 asmp_snapshot/asmp_query 查看环境模型、"
            "威胁假设、未知项和任务建议；它是参考模型，不是执行指令。"
        )

    def context_block(self, session_id: str) -> str:
        if self.service is None:
            return ""
        snap = self.service.get_model_snapshot(session_id)
        if not snap.get("nodes") and not snap.get("hypotheses"):
            return ""
        # 让模型驱动的任务建议进入 worker 的只读上下文；是否执行仍由
        # agent/Reason 与 Harness 授权决定。
        try:
            self.service.propose_tasks(session_id, limit=5)
        except Exception:  # noqa: BLE001
            pass
        return self.service.brief(session_id, max_chars=3000)

    def ui_manifest(self) -> dict[str, Any]:
        return {
            "panel": "situation",
            "title": "攻击态势",
            "description": "节点/关系/假设/未知项/任务建议",
        }
