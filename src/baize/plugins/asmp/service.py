"""ASMP facade: public plugin interface used by Harness and tools."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from baize.plugins.base import PluginEvent

from .analyzers import analyze_model_delta, analyze_node
from .artifacts import ArtifactStore
from .builder import ModelBuilder
from .db import ASMPDatabase
from .models import (
    Evidence, HYPOTHESIS_STATUSES, Node, Relation, ThreatHypothesis, Unknown, now,
)
from .normalizer import (
    Observation, observation_from_dict, observation_from_human,
    observation_from_tool_result,
)
from .planner import apply_execution_result, propose_tasks
from .reasoner import infer_paths, relation_gaps
from .types import (
    propose_type, register_type, seed_types, validate_type_definition,
)

logger = logging.getLogger("baize.plugins.asmp.service")


class ASMPService:
    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(self.data_dir / "artifacts")
        self._lock = threading.RLock()
        self._dbs: dict[str, ASMPDatabase] = {}
        self._builders: dict[str, ModelBuilder] = {}
        self._pending_calls: dict[str, dict[str, dict[str, Any]]] = {}
        self._processed: dict[str, set[str]] = {}

    # ---- lifecycle -----------------------------------------------------
    def _db(self, engagement_id: str) -> ASMPDatabase:
        with self._lock:
            db = self._dbs.get(engagement_id)
            if db is None:
                safe = "".join(c for c in engagement_id if c.isalnum() or c in "-_")
                db = ASMPDatabase(self.data_dir / f"{safe or 'engagement'}.sqlite")
                seed_types(db)
                db.ensure_engagement(engagement_id)
                self._dbs[engagement_id] = db
                self._builders[engagement_id] = ModelBuilder(db)
            return db

    def close(self) -> None:
        with self._lock:
            for db in self._dbs.values():
                db.close()
            self._dbs.clear()
            self._builders.clear()

    # ---- standardized interface ---------------------------------------
    def initialize(self, engagement_id: str) -> dict[str, Any]:
        db = self._db(engagement_id)
        return {"engagement_id": engagement_id, "types": len(db.list_types())}

    def ingest_observation(self, engagement_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        obs = observation_from_dict(engagement_id, payload)
        return self._ingest(obs)

    def _ingest(self, obs: Observation) -> dict[str, Any]:
        db = self._db(obs.engagement_id)
        builder = self._builders[obs.engagement_id]
        # Preserve the raw normalized observation on disk; the Evidence row
        # keeps only a reference plus a short human-readable excerpt.
        try:
            obs.raw_artifact_ref = self.artifacts.put(
                obs.engagement_id, obs.observation_id, obs.to_dict()
            )
        except OSError:
            logger.debug("ASMP artifact 写入失败", exc_info=True)
        result = builder.ingest(obs)
        node_ids = list(dict.fromkeys(
            result.get("nodes_created", []) + result.get("nodes_updated", [])
        ))
        analysis = analyze_model_delta(db, obs.engagement_id, node_ids) if node_ids else {
            "analyzed": 0, "results": [],
        }
        # Relation gaps are cheap and useful immediately after auth/web data.
        gaps = relation_gaps(db, obs.engagement_id) if any(
            (db.get_node(nid) or Node(id="", engagement_id="", type_id="", name="")).type_id
            in ("authentication_service", "web_application", "api_service")
            for nid in node_ids
        ) else []
        result["analysis"] = analysis
        result["relation_gaps"] = [g.id for g in gaps]
        result["operation_id"] = f"op_{obs.observation_id}"
        result["status"] = "completed"
        return result

    def get_model_snapshot(self, engagement_id: str) -> dict[str, Any]:
        db = self._db(engagement_id)
        snap = db.snapshot(engagement_id)
        snap["summary"] = {
            "nodes": len(snap["nodes"]),
            "relations": len(snap["relations"]),
            "evidence": len(snap["evidence"]),
            "hypotheses": len(snap["hypotheses"]),
            "unknowns": len(snap["unknowns"]),
            "tasks": len(snap["tasks"]),
        }
        return snap

    def query_nodes(self, engagement_id: str, type_id: str = "",
                    status: str = "") -> list[dict[str, Any]]:
        return [n.to_dict() for n in self._db(engagement_id).list_nodes(
            engagement_id, type_id=type_id, status=status
        )]

    def query_relations(self, engagement_id: str, source_id: str = "",
                        target_id: str = "", type_id: str = "") -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._db(engagement_id).list_relations(
            engagement_id, source_id=source_id, target_id=target_id, type_id=type_id
        )]

    def get_hypotheses(self, engagement_id: str, status: str = "",
                       category: str = "") -> list[dict[str, Any]]:
        return [h.to_dict() for h in self._db(engagement_id).list_hypotheses(
            engagement_id, status=status, category=category
        )]

    def get_unknowns(self, engagement_id: str) -> list[dict[str, Any]]:
        return [u.to_dict() for u in self._db(engagement_id).list_unknowns(engagement_id)]

    def update_unknown(self, engagement_id: str, unknown_id: str, *,
                       status: str = "resolved", note: str = "") -> dict[str, Any]:
        db = self._db(engagement_id)
        unknown = db.get_unknown(unknown_id)
        if unknown is None:
            return {"ok": False, "error": "未知项不存在"}
        if status not in ("open", "resolved", "accepted_risk", "not_applicable", "closed"):
            return {"ok": False, "error": f"非法状态: {status}"}
        unknown.status = status
        unknown.reason = note or unknown.reason
        unknown.updated_at = now()
        saved, _ = db.upsert_unknown(unknown)
        db.record_change(
            engagement_id, operation="unknown_status", object_type="unknown",
            object_id=saved.id, before=None, after={"status": status, "note": note},
            reason=note or "unknown transition", actor="agent",
        )
        return {"ok": True, "unknown": saved.to_dict()}

    def analyze_node(self, engagement_id: str, node_id: str) -> dict[str, Any]:
        db = self._db(engagement_id)
        node = db.get_node(node_id)
        if node is None:
            return {"ok": False, "error": "节点不存在"}
        return analyze_node(db, node, db.list_relations(engagement_id))

    def analyze_model_delta(self, engagement_id: str,
                            node_ids: list[str]) -> dict[str, Any]:
        return analyze_model_delta(self._db(engagement_id), engagement_id, node_ids)

    def get_unknowns_and_gaps(self, engagement_id: str) -> list[dict[str, Any]]:
        db = self._db(engagement_id)
        relation_gaps(db, engagement_id)
        return [u.to_dict() for u in db.list_unknowns(engagement_id)]

    def propose_tasks(self, engagement_id: str, limit: int = 10) -> list[dict[str, Any]]:
        db = self._db(engagement_id)
        # Path inference is bounded and only runs when tasks are requested.
        infer_paths(db, engagement_id)
        relation_gaps(db, engagement_id)
        return [t.to_dict() for t in propose_tasks(db, engagement_id, limit=limit)]

    def apply_execution_result(self, engagement_id: str, task_id: str,
                               status: str = "completed", note: str = "",
                               observation: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        db = self._db(engagement_id)
        task = apply_execution_result(db, task_id, status=status, note=note)
        if task is None:
            return {"ok": False, "error": "任务不存在"}
        result: dict[str, Any] = {"ok": True, "task": task.to_dict()}
        if observation:
            result["ingest"] = self.ingest_observation(engagement_id, observation)
        return result

    def update_hypothesis(self, engagement_id: str, hypothesis_id: str, *,
                          status: str, evidence_id: str = "",
                          contradicting_evidence_id: str = "",
                          note: str = "", confidence: float | None = None) -> dict[str, Any]:
        db = self._db(engagement_id)
        h = db.get_hypothesis(hypothesis_id)
        if h is None:
            return {"ok": False, "error": "假设不存在"}
        if status not in HYPOTHESIS_STATUSES:
            return {"ok": False, "error": f"非法状态: {status}"}
        if status == "confirmed" and not (h.supporting_evidence_refs or evidence_id):
            return {"ok": False, "error": "确认假设需要至少一条支持证据"}
        if evidence_id and evidence_id not in h.supporting_evidence_refs:
            h.supporting_evidence_refs.append(evidence_id)
        if contradicting_evidence_id and contradicting_evidence_id not in h.contradicting_evidence_refs:
            h.contradicting_evidence_refs.append(contradicting_evidence_id)
        h.status = status
        if confidence is not None:
            h.confidence = max(0.0, min(1.0, float(confidence)))
        h.updated_at = now()
        h.version += 1
        saved, _ = db.upsert_hypothesis(h)
        db.record_change(
            engagement_id, operation="hypothesis_status", object_type="hypothesis",
            object_id=saved.id, before=None,
            after={
                "status": status,
                "evidence_id": evidence_id,
                "contradicting_evidence_id": contradicting_evidence_id,
                "note": note,
            },
            reason=note or "hypothesis transition", actor="agent",
        )
        return {"ok": True, "hypothesis": saved.to_dict()}

    def get_change_history(self, engagement_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._db(engagement_id).list_changes(engagement_id, limit=limit)

    def get_artifact(self, ref: str) -> Any:
        return self.artifacts.get(ref)

    def propose_type_definition(self, engagement_id: str,
                                definition: dict[str, Any], reason: str = "") -> dict[str, Any]:
        return propose_type(self._db(engagement_id), definition, reason=reason)

    def validate_type_definition(self, engagement_id: str,
                                 definition: dict[str, Any]) -> dict[str, Any]:
        db = self._db(engagement_id)
        errors = validate_type_definition(definition, db.list_types())
        return {"ok": not errors, "errors": errors}

    def register_type(self, engagement_id: str, type_id: str) -> dict[str, Any]:
        return register_type(self._db(engagement_id), type_id)

    def get_types(self, engagement_id: str, kind: str = "") -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._db(engagement_id).list_types(kind=kind)]

    # ---- event lifecycle ----------------------------------------------
    def on_event(self, event: PluginEvent) -> None:
        sid = getattr(event, "session_id", "")
        kind = getattr(event, "kind", "")
        payload = getattr(event, "payload", {}) or {}
        if not sid:
            return
        try:
            if kind == "user/message":
                content = str(payload.get("content") or "")
                if content:
                    self._ingest(observation_from_human(sid, content))
            elif kind == "tool/call":
                call_id = str(payload.get("call_id") or "")
                if not call_id:
                    call_id = f"{payload.get('name') or 'call'}:{len(self._pending_calls.get(sid, {}))}"
                self._pending_calls.setdefault(sid, {})[call_id] = {
                    "name": str(payload.get("name") or ""),
                    "arguments": payload.get("arguments") or {},
                    "call_id": call_id,
                }
            elif kind in ("tool/result", "tool/error"):
                call_id = str(payload.get("call_id") or "")
                pending_map = self._pending_calls.get(sid, {})
                pending = pending_map.pop(call_id, {}) if call_id else {}
                if not pending:
                    # 旧 SessionLog 的 tool/result 可能没有 call_id；按工具名
                    # 回退到最近一条未配对的调用，避免回放时丢失请求参数。
                    name_hint = str(payload.get("name") or "")
                    for key in reversed(list(pending_map.keys())):
                        candidate = pending_map[key]
                        if not name_hint or candidate.get("name") == name_hint:
                            pending = pending_map.pop(key, {})
                            call_id = str(pending.get("call_id") or key)
                            break
                name = str(payload.get("name") or pending.get("name") or "")
                if call_id and call_id in self._processed.setdefault(sid, set()):
                    return
                obs = observation_from_tool_result(
                    engagement_id=sid,
                    name=name,
                    output=str(payload.get("output") or ""),
                    arguments=pending.get("arguments") or {},
                    call_id=call_id or name,
                    status="failed" if (kind == "tool/error" or payload.get("denied")) else "done",
                    scope_ref=sid,
                )
                self._ingest(obs)
                if call_id:
                    self._processed.setdefault(sid, set()).add(call_id)
        except Exception:  # noqa: BLE001
            logger.warning("ASMP 事件处理失败 kind=%s session=%s", kind, sid, exc_info=True)

    # ---- historical replay --------------------------------------------
    def replay_from_log(self, engagement_id: str, path: str | Path) -> bool:
        """Build an initial model from an existing SessionLog JSONL.

        Only used when the model is empty. Event logs remain the append-only
        truth; replay is a deterministic projection.
        """
        db = self._db(engagement_id)
        if db.list_nodes(engagement_id) or db.list_evidence(engagement_id, limit=1):
            return False
        try:
            p = Path(path)
            if not p.exists():
                return False
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = str(raw.get("kind") or "")
                if not kind:
                    continue
                self.on_event(PluginEvent(
                    session_id=engagement_id, kind=kind,
                    payload=raw.get("payload") or {}, source="session_log",
                ))
            return True
        except Exception:  # noqa: BLE001
            logger.debug("ASMP 日志回放失败 session=%s", engagement_id, exc_info=True)
            return False

    # ---- LLM-facing projections ---------------------------------------
    def brief(self, engagement_id: str, max_chars: int = 5000) -> str:
        snap = self.get_model_snapshot(engagement_id)
        nodes = snap["nodes"]
        hyps = snap["hypotheses"]
        unknowns = snap["unknowns"]
        tasks = snap["tasks"]
        relations = snap["relations"]
        lines = [f"# 攻击态势模型 {engagement_id}"]
        if nodes:
            lines.append("\n## 已知环境")
            for n in nodes[:12]:
                lines.append(
                    f"- [{n['type_id']}] {n['name']} ({n['status']}, v{n['version']})"
                )
        if relations:
            lines.append("\n## 关系")
            by_id = {n["id"]: n["name"] for n in nodes}
            for r in relations[:10]:
                lines.append(
                    f"- {by_id.get(r['source_id'], r['source_id'])} "
                    f"--{r['type_id']}--> {by_id.get(r['target_id'], r['target_id'])} "
                    f"({r['status']}, conf={r['confidence']:.2f})"
                )
        open_h = [h for h in hyps if h["status"] in ("candidate", "ready", "in_progress", "blocked", "inconclusive")]
        if open_h:
            lines.append("\n## 威胁假设（未确认）")
            for h in open_h[:10]:
                lines.append(
                    f"- [{h['status']}/{h['category']}] {h['title']} "
                    f"(conf={h['confidence']:.2f})"
                )
        if unknowns:
            lines.append("\n## 未知项")
            for u in unknowns[:10]:
                lines.append(f"- {u['attribute']}: {u['reason']} (priority={u['priority']:.2f})")
        if tasks:
            lines.append("\n## 已提议任务")
            for t in tasks[:8]:
                lines.append(f"- [{t['status']}] {t['objective']} (priority={t['priority']:.2f})")
        lines.append("\n## 说明")
        lines.append("以上是模型投影，不是漏洞结论；关系/路径必须由证据和实际测试验证。")
        return "\n".join(lines)[:max_chars]

    def context_block(self, engagement_id: str) -> str:
        snap = self.get_model_snapshot(engagement_id)
        if not snap.get("nodes") and not snap.get("hypotheses"):
            return ""
        return self.brief(engagement_id, max_chars=2600)
