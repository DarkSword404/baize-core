"""SQLite storage for ASMP V1.0.

One SQLite file per engagement/session. JSON columns carry type-specific
attributes; stable lookup fields are normal columns. This is intentionally not
a graph database: V1.0 keeps relational storage and explicit objects.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from baize.memory.utils import new_id, now_iso

from .models import (
    Evidence, ExplorationTask, ModelChange, Node, Relation, ThreatHypothesis,
    TypeDefinition, Unknown,
)

logger = logging.getLogger("baize.plugins.asmp.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_meta (
    engagement_id TEXT PRIMARY KEY, model_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS model_nodes (
    id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, type_id TEXT NOT NULL,
    name TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
    attributes TEXT NOT NULL DEFAULT '{}', evidence_refs TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'discovered', created_at TEXT, updated_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(engagement_id, type_id, name)
);
CREATE TABLE IF NOT EXISTS model_relations (
    id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, source_id TEXT NOT NULL,
    target_id TEXT NOT NULL, type_id TEXT NOT NULL, direction TEXT DEFAULT 'directed',
    conditions TEXT NOT NULL DEFAULT '[]', evidence_refs TEXT NOT NULL DEFAULT '[]',
    confidence REAL DEFAULT 0.5, status TEXT DEFAULT 'observed',
    validity TEXT NOT NULL DEFAULT '{}', version INTEGER DEFAULT 1,
    created_at TEXT, updated_at TEXT,
    UNIQUE(engagement_id, source_id, target_id, type_id)
);
CREATE TABLE IF NOT EXISTS model_evidence (
    id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, source_kind TEXT,
    source_ref TEXT, subject_refs TEXT NOT NULL DEFAULT '[]',
    observation TEXT NOT NULL DEFAULT '', raw_artifact_ref TEXT,
    observed_at TEXT, scope_ref TEXT, reliability TEXT, status TEXT,
    UNIQUE(engagement_id, source_ref, id)
);
CREATE TABLE IF NOT EXISTS model_hypotheses (
    id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, title TEXT, category TEXT,
    target_refs TEXT NOT NULL DEFAULT '[]', claim TEXT,
    supporting_evidence_refs TEXT NOT NULL DEFAULT '[]',
    contradicting_evidence_refs TEXT NOT NULL DEFAULT '[]',
    preconditions TEXT NOT NULL DEFAULT '[]', impact TEXT NOT NULL DEFAULT '{}',
    confidence REAL DEFAULT 0.4, status TEXT DEFAULT 'candidate',
    next_action_refs TEXT NOT NULL DEFAULT '[]', created_at TEXT, updated_at TEXT,
    version INTEGER DEFAULT 1, key TEXT NOT NULL,
    UNIQUE(engagement_id, key)
);
CREATE TABLE IF NOT EXISTS model_hypothesis_evidence (
    hypothesis_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'supports',
    PRIMARY KEY(hypothesis_id, evidence_id, relation)
);
CREATE TABLE IF NOT EXISTS model_unknowns (
    id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, target_refs TEXT DEFAULT '[]',
    attribute TEXT, reason TEXT, affects TEXT DEFAULT '[]', verifiable INTEGER DEFAULT 1,
    suggested_evidence TEXT DEFAULT '[]', priority REAL DEFAULT 0.5,
    status TEXT DEFAULT 'open', created_at TEXT, updated_at TEXT, key TEXT NOT NULL,
    UNIQUE(engagement_id, key)
);
CREATE TABLE IF NOT EXISTS model_tasks (
    id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, objective TEXT,
    target_refs TEXT DEFAULT '[]', hypothesis_refs TEXT DEFAULT '[]',
    required_evidence TEXT DEFAULT '[]', preconditions TEXT DEFAULT '[]',
    risk_level TEXT DEFAULT 'low', estimated_cost INTEGER DEFAULT 2,
    priority REAL DEFAULT 0.5, status TEXT DEFAULT 'proposed',
    stop_conditions TEXT DEFAULT '[]', created_at TEXT, updated_at TEXT,
    key TEXT NOT NULL, UNIQUE(engagement_id, key)
);
CREATE TABLE IF NOT EXISTS model_types (
    type_id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'node',
    display_name TEXT, schema_version INTEGER DEFAULT 1, base_type TEXT,
    attributes TEXT DEFAULT '{}', threat_profile TEXT DEFAULT '[]',
    analysis_version INTEGER DEFAULT 1, status TEXT DEFAULT 'builtin',
    description TEXT DEFAULT '', created_at TEXT, updated_at TEXT,
    version INTEGER DEFAULT 1, proposal_reason TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS model_type_versions (
    type_id TEXT NOT NULL, version INTEGER NOT NULL, definition TEXT NOT NULL,
    created_at TEXT, PRIMARY KEY(type_id, version)
);
CREATE TABLE IF NOT EXISTS model_changes (
    id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, operation TEXT,
    object_type TEXT, object_id TEXT, before TEXT, after TEXT,
    reason TEXT, actor TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS model_analysis_runs (
    id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, analyzer TEXT,
    target_refs TEXT DEFAULT '[]', status TEXT, output TEXT DEFAULT '{}',
    started_at TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_engagement ON model_nodes(engagement_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON model_nodes(engagement_id, type_id);
CREATE INDEX IF NOT EXISTS idx_relations_engagement ON model_relations(engagement_id);
CREATE INDEX IF NOT EXISTS idx_hypotheses_engagement ON model_hypotheses(engagement_id);
CREATE INDEX IF NOT EXISTS idx_tasks_engagement ON model_tasks(engagement_id);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


class ASMPDatabase:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def ensure_engagement(self, engagement_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO model_meta(engagement_id,model_version,created_at,updated_at) VALUES(?,?,?,?)",
                (engagement_id, 0, now_iso(), now_iso()),
            )
            row = self._conn.execute(
                "SELECT model_version FROM model_meta WHERE engagement_id=?",
                (engagement_id,),
            ).fetchone()
            if row is not None and int(row["model_version"] or 0) == 0:
                count = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM model_changes WHERE engagement_id=?",
                    (engagement_id,),
                ).fetchone()
                if count and int(count["n"] or 0) > 0:
                    self._conn.execute(
                        "UPDATE model_meta SET model_version=?, updated_at=? WHERE engagement_id=?",
                        (int(count["n"]), now_iso(), engagement_id),
                    )
            self._conn.commit()

    def bump_model_version(self, engagement_id: str) -> int:
        with self._lock:
            self._conn.execute(
                "UPDATE model_meta SET model_version=model_version+1, updated_at=? WHERE engagement_id=?",
                (now_iso(), engagement_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT model_version FROM model_meta WHERE engagement_id=?",
                (engagement_id,),
            ).fetchone()
            return int(row["model_version"] if row else 0)

    def get_model_version(self, engagement_id: str) -> int:
        rows = self._fetchall(
            "SELECT model_version FROM model_meta WHERE engagement_id=?",
            (engagement_id,),
        )
        return int(rows[0]["model_version"] or 0) if rows else 0

    # ---- nodes ---------------------------------------------------------
    def upsert_node(self, node: Node) -> tuple[Node, bool]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM model_nodes WHERE engagement_id=? AND type_id=? AND name=?",
                (node.engagement_id, node.type_id, node.name),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO model_nodes(id,engagement_id,type_id,name,summary,attributes,evidence_refs,status,created_at,updated_at,version)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (node.id, node.engagement_id, node.type_id, node.name,
                     node.summary, _dumps(node.attributes), _dumps(node.evidence_refs),
                     node.status, node.created_at, node.updated_at, node.version),
                )
                self._conn.commit()
                return node, True
            old = self._node_from_row(row)
            old.attributes.update(node.attributes)
            for ev in node.evidence_refs:
                if ev not in old.evidence_refs:
                    old.evidence_refs.append(ev)
            if node.summary:
                old.summary = node.summary
            old.status = node.status or old.status
            old.updated_at = node.updated_at
            old.version += 1
            self._conn.execute(
                "UPDATE model_nodes SET summary=?,attributes=?,evidence_refs=?,status=?,updated_at=?,version=? WHERE id=?",
                (old.summary, _dumps(old.attributes), _dumps(old.evidence_refs),
                 old.status, old.updated_at, old.version, old.id),
            )
            self._conn.commit()
            return old, False

    @staticmethod
    def _node_from_row(row: sqlite3.Row) -> Node:
        return Node(
            id=row["id"], engagement_id=row["engagement_id"], type_id=row["type_id"],
            name=row["name"], summary=row["summary"],
            attributes=_loads(row["attributes"], {}),
            evidence_refs=_loads(row["evidence_refs"], []),
            status=row["status"], created_at=row["created_at"],
            updated_at=row["updated_at"], version=int(row["version"] or 1),
        )

    def get_node(self, node_id: str) -> Optional[Node]:
        rows = self._fetchall("SELECT * FROM model_nodes WHERE id=?", (node_id,))
        return self._node_from_row(rows[0]) if rows else None

    def list_nodes(self, engagement_id: str, type_id: str = "",
                   status: str = "") -> list[Node]:
        sql = "SELECT * FROM model_nodes WHERE engagement_id=?"
        args: list[Any] = [engagement_id]
        if type_id:
            sql += " AND type_id=?"
            args.append(type_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY updated_at DESC"
        return [self._node_from_row(r) for r in self._fetchall(sql, tuple(args))]

    # ---- relations -----------------------------------------------------
    def upsert_relation(self, rel: Relation) -> tuple[Relation, bool]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM model_relations WHERE engagement_id=? AND source_id=? AND target_id=? AND type_id=?",
                (rel.engagement_id, rel.source_id, rel.target_id, rel.type_id),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO model_relations(id,engagement_id,source_id,target_id,type_id,direction,conditions,evidence_refs,confidence,status,validity,version,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rel.id, rel.engagement_id, rel.source_id, rel.target_id, rel.type_id,
                     rel.direction, _dumps(rel.conditions), _dumps(rel.evidence_refs),
                     rel.confidence, rel.status, _dumps(rel.validity), rel.version,
                     rel.created_at, rel.updated_at),
                )
                self._conn.commit()
                return rel, True
            old = self._relation_from_row(row)
            for ev in rel.evidence_refs:
                if ev not in old.evidence_refs:
                    old.evidence_refs.append(ev)
            old.conditions = old.conditions or rel.conditions
            old.confidence = max(old.confidence, rel.confidence)
            old.status = rel.status or old.status
            old.updated_at = rel.updated_at
            old.version += 1
            self._conn.execute(
                "UPDATE model_relations SET conditions=?,evidence_refs=?,confidence=?,status=?,validity=?,updated_at=?,version=? WHERE id=?",
                (_dumps(old.conditions), _dumps(old.evidence_refs), old.confidence,
                 old.status, _dumps(old.validity), old.updated_at, old.version, old.id),
            )
            self._conn.commit()
            return old, False

    @staticmethod
    def _relation_from_row(row: sqlite3.Row) -> Relation:
        return Relation(
            id=row["id"], engagement_id=row["engagement_id"],
            source_id=row["source_id"], target_id=row["target_id"],
            type_id=row["type_id"], direction=row["direction"],
            conditions=_loads(row["conditions"], []),
            evidence_refs=_loads(row["evidence_refs"], []),
            confidence=float(row["confidence"] or 0.5), status=row["status"],
            validity=_loads(row["validity"], {}), version=int(row["version"] or 1),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def list_relations(self, engagement_id: str, source_id: str = "",
                       target_id: str = "", type_id: str = "") -> list[Relation]:
        sql = "SELECT * FROM model_relations WHERE engagement_id=?"
        args: list[Any] = [engagement_id]
        if source_id:
            sql += " AND source_id=?"
            args.append(source_id)
        if target_id:
            sql += " AND target_id=?"
            args.append(target_id)
        if type_id:
            sql += " AND type_id=?"
            args.append(type_id)
        sql += " ORDER BY updated_at DESC"
        return [self._relation_from_row(r) for r in self._fetchall(sql, tuple(args))]

    # ---- evidence ------------------------------------------------------
    def add_evidence(self, ev: Evidence) -> Evidence:
        self._execute(
            "INSERT OR IGNORE INTO model_evidence(id,engagement_id,source_kind,source_ref,subject_refs,observation,raw_artifact_ref,observed_at,scope_ref,reliability,status)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ev.id, ev.engagement_id, ev.source_kind, ev.source_ref,
             _dumps(ev.subject_refs), ev.observation, ev.raw_artifact_ref,
             ev.observed_at, ev.scope_ref, ev.reliability, ev.status),
        )
        return ev

    def list_evidence(self, engagement_id: str, limit: int = 200) -> list[Evidence]:
        rows = self._fetchall(
            "SELECT * FROM model_evidence WHERE engagement_id=? ORDER BY observed_at DESC LIMIT ?",
            (engagement_id, limit),
        )
        out = []
        for r in rows:
            out.append(Evidence(
                id=r["id"], engagement_id=r["engagement_id"],
                source_kind=r["source_kind"], source_ref=r["source_ref"],
                subject_refs=_loads(r["subject_refs"], []),
                observation=r["observation"], raw_artifact_ref=r["raw_artifact_ref"],
                observed_at=r["observed_at"], scope_ref=r["scope_ref"],
                reliability=r["reliability"], status=r["status"],
            ))
        return out

    # ---- hypotheses / unknowns / tasks ---------------------------------
    def upsert_hypothesis(self, h: ThreatHypothesis) -> tuple[ThreatHypothesis, bool]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM model_hypotheses WHERE engagement_id=? AND key=?",
                (h.engagement_id, h.key),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO model_hypotheses(id,engagement_id,title,category,target_refs,claim,supporting_evidence_refs,contradicting_evidence_refs,preconditions,impact,confidence,status,next_action_refs,created_at,updated_at,version,key)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (h.id, h.engagement_id, h.title, h.category, _dumps(h.target_refs),
                     h.claim, _dumps(h.supporting_evidence_refs),
                     _dumps(h.contradicting_evidence_refs), _dumps(h.preconditions),
                     _dumps(h.impact), h.confidence, h.status,
                     _dumps(h.next_action_refs), h.created_at, h.updated_at,
                     h.version, h.key),
                )
                self._conn.commit()
                self._link_hypothesis_evidence(h)
                return h, True
            old = self._hypothesis_from_row(row)
            for ev in h.supporting_evidence_refs:
                if ev not in old.supporting_evidence_refs:
                    old.supporting_evidence_refs.append(ev)
            for ev in h.contradicting_evidence_refs:
                if ev not in old.contradicting_evidence_refs:
                    old.contradicting_evidence_refs.append(ev)
            for target in h.target_refs:
                if target not in old.target_refs:
                    old.target_refs.append(target)
            old.status = h.status or old.status
            old.confidence = max(old.confidence, h.confidence)
            old.impact = h.impact or old.impact
            old.preconditions = h.preconditions or old.preconditions
            old.next_action_refs = h.next_action_refs or old.next_action_refs
            old.updated_at = h.updated_at
            old.version += 1
            self._conn.execute(
                "UPDATE model_hypotheses SET status=?,confidence=?,impact=?,preconditions=?,next_action_refs=?,supporting_evidence_refs=?,contradicting_evidence_refs=?,updated_at=?,version=? WHERE id=?",
                (old.status, old.confidence, _dumps(old.impact),
                 _dumps(old.preconditions), _dumps(old.next_action_refs),
                 _dumps(old.supporting_evidence_refs),
                 _dumps(old.contradicting_evidence_refs), old.updated_at,
                 old.version, old.id),
            )
            self._conn.commit()
            self._link_hypothesis_evidence(old)
            return old, False

    def _link_hypothesis_evidence(self, h: ThreatHypothesis) -> None:
        for ev in h.supporting_evidence_refs:
            self._execute(
                "INSERT OR IGNORE INTO model_hypothesis_evidence(hypothesis_id,evidence_id,relation) VALUES(?,?,?)",
                (h.id, ev, "supports"),
            )
        for ev in h.contradicting_evidence_refs:
            self._execute(
                "INSERT OR IGNORE INTO model_hypothesis_evidence(hypothesis_id,evidence_id,relation) VALUES(?,?,?)",
                (h.id, ev, "contradicts"),
            )

    @staticmethod
    def _hypothesis_from_row(row: sqlite3.Row) -> ThreatHypothesis:
        return ThreatHypothesis(
            id=row["id"], engagement_id=row["engagement_id"], title=row["title"],
            category=row["category"], target_refs=_loads(row["target_refs"], []),
            claim=row["claim"],
            supporting_evidence_refs=_loads(row["supporting_evidence_refs"], []),
            contradicting_evidence_refs=_loads(row["contradicting_evidence_refs"], []),
            preconditions=_loads(row["preconditions"], []),
            impact=_loads(row["impact"], {}), confidence=float(row["confidence"] or 0.4),
            status=row["status"], next_action_refs=_loads(row["next_action_refs"], []),
            created_at=row["created_at"], updated_at=row["updated_at"],
            version=int(row["version"] or 1), key=row["key"],
        )

    def list_hypotheses(self, engagement_id: str, status: str = "",
                        category: str = "") -> list[ThreatHypothesis]:
        sql = "SELECT * FROM model_hypotheses WHERE engagement_id=?"
        args: list[Any] = [engagement_id]
        if status:
            sql += " AND status=?"
            args.append(status)
        if category:
            sql += " AND category=?"
            args.append(category)
        sql += " ORDER BY confidence DESC, updated_at DESC"
        return [self._hypothesis_from_row(r) for r in self._fetchall(sql, tuple(args))]

    def get_hypothesis(self, hypothesis_id: str) -> Optional[ThreatHypothesis]:
        rows = self._fetchall("SELECT * FROM model_hypotheses WHERE id=?", (hypothesis_id,))
        return self._hypothesis_from_row(rows[0]) if rows else None

    def upsert_unknown(self, u: Unknown) -> tuple[Unknown, bool]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM model_unknowns WHERE engagement_id=? AND key=?",
                (u.engagement_id, u.key),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO model_unknowns(id,engagement_id,target_refs,attribute,reason,affects,verifiable,suggested_evidence,priority,status,created_at,updated_at,key)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (u.id, u.engagement_id, _dumps(u.target_refs), u.attribute,
                     u.reason, _dumps(u.affects), 1 if u.verifiable else 0,
                     _dumps(u.suggested_evidence), u.priority, u.status,
                     u.created_at, u.updated_at, u.key),
                )
                self._conn.commit()
                return u, True
            old = self._unknown_from_row(row)
            for target in u.target_refs:
                if target not in old.target_refs:
                    old.target_refs.append(target)
            for affect in u.affects:
                if affect not in old.affects:
                    old.affects.append(affect)
            for evidence in u.suggested_evidence:
                if evidence not in old.suggested_evidence:
                    old.suggested_evidence.append(evidence)
            old.priority = max(old.priority, u.priority)
            old.status = u.status or old.status
            old.reason = u.reason or old.reason
            old.updated_at = u.updated_at
            self._conn.execute(
                "UPDATE model_unknowns SET target_refs=?,affects=?,suggested_evidence=?,priority=?,status=?,reason=?,updated_at=? WHERE id=?",
                (_dumps(old.target_refs), _dumps(old.affects),
                 _dumps(old.suggested_evidence), old.priority, old.status,
                 old.reason, old.updated_at, old.id),
            )
            self._conn.commit()
            return old, False

    @staticmethod
    def _unknown_from_row(row: sqlite3.Row) -> Unknown:
        return Unknown(
            id=row["id"], engagement_id=row["engagement_id"],
            target_refs=_loads(row["target_refs"], []), attribute=row["attribute"],
            reason=row["reason"], affects=_loads(row["affects"], []),
            verifiable=bool(row["verifiable"]),
            suggested_evidence=_loads(row["suggested_evidence"], []),
            priority=float(row["priority"] or 0.5), status=row["status"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            key=row["key"],
        )

    def list_unknowns(self, engagement_id: str) -> list[Unknown]:
        return [self._unknown_from_row(r) for r in self._fetchall(
            "SELECT * FROM model_unknowns WHERE engagement_id=? ORDER BY priority DESC, updated_at DESC",
            (engagement_id,),
        )]

    def get_unknown(self, unknown_id: str) -> Optional[Unknown]:
        rows = self._fetchall("SELECT * FROM model_unknowns WHERE id=?", (unknown_id,))
        return self._unknown_from_row(rows[0]) if rows else None

    def upsert_task(self, t: ExplorationTask) -> tuple[ExplorationTask, bool]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM model_tasks WHERE engagement_id=? AND key=?",
                (t.engagement_id, t.key),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO model_tasks(id,engagement_id,objective,target_refs,hypothesis_refs,required_evidence,preconditions,risk_level,estimated_cost,priority,status,stop_conditions,created_at,updated_at,key)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (t.id, t.engagement_id, t.objective, _dumps(t.target_refs),
                     _dumps(t.hypothesis_refs), _dumps(t.required_evidence),
                     _dumps(t.preconditions), t.risk_level, t.estimated_cost,
                     t.priority, t.status, _dumps(t.stop_conditions),
                     t.created_at, t.updated_at, t.key),
                )
                self._conn.commit()
                return t, True
            old = self._task_from_row(row)
            old.priority = max(old.priority, t.priority)
            old.status = t.status or old.status
            old.updated_at = t.updated_at
            self._conn.execute(
                "UPDATE model_tasks SET priority=?,status=?,updated_at=? WHERE id=?",
                (old.priority, old.status, old.updated_at, old.id),
            )
            self._conn.commit()
            return old, False

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> ExplorationTask:
        return ExplorationTask(
            id=row["id"], engagement_id=row["engagement_id"],
            objective=row["objective"], target_refs=_loads(row["target_refs"], []),
            hypothesis_refs=_loads(row["hypothesis_refs"], []),
            required_evidence=_loads(row["required_evidence"], []),
            preconditions=_loads(row["preconditions"], []),
            risk_level=row["risk_level"], estimated_cost=int(row["estimated_cost"] or 2),
            priority=float(row["priority"] or 0.5), status=row["status"],
            stop_conditions=_loads(row["stop_conditions"], []),
            created_at=row["created_at"], updated_at=row["updated_at"],
            key=row["key"],
        )

    def list_tasks(self, engagement_id: str, status: str = "") -> list[ExplorationTask]:
        sql = "SELECT * FROM model_tasks WHERE engagement_id=?"
        args: list[Any] = [engagement_id]
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY priority DESC, updated_at DESC"
        return [self._task_from_row(r) for r in self._fetchall(sql, tuple(args))]

    def get_task(self, task_id: str) -> Optional[ExplorationTask]:
        rows = self._fetchall("SELECT * FROM model_tasks WHERE id=?", (task_id,))
        return self._task_from_row(rows[0]) if rows else None

    # ---- types / changes / analysis ------------------------------------
    def upsert_type(self, t: TypeDefinition) -> TypeDefinition:
        self._execute(
            "INSERT INTO model_types(type_id,kind,display_name,schema_version,base_type,attributes,threat_profile,analysis_version,status,description,created_at,updated_at,version,proposal_reason)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(type_id) DO UPDATE SET kind=excluded.kind,display_name=excluded.display_name,"
            "schema_version=excluded.schema_version,base_type=excluded.base_type,attributes=excluded.attributes,"
            "threat_profile=excluded.threat_profile,analysis_version=excluded.analysis_version,status=excluded.status,"
            "description=excluded.description,updated_at=excluded.updated_at,version=excluded.version,proposal_reason=excluded.proposal_reason",
            (t.type_id, t.kind, t.display_name, t.schema_version, t.base_type,
             _dumps(t.attributes), _dumps(t.threat_profile), t.analysis_version,
             t.status, t.description, t.created_at, t.updated_at, t.version,
             t.proposal_reason),
        )
        self._execute(
            "INSERT OR REPLACE INTO model_type_versions(type_id,version,definition,created_at) VALUES(?,?,?,?)",
            (t.type_id, t.version, _dumps(t.to_dict()), now_iso()),
        )
        return t

    def get_type(self, type_id: str) -> Optional[TypeDefinition]:
        rows = self._fetchall("SELECT * FROM model_types WHERE type_id=?", (type_id,))
        return self._type_from_row(rows[0]) if rows else None

    def list_types(self, kind: str = "") -> list[TypeDefinition]:
        sql = "SELECT * FROM model_types"
        args: tuple = ()
        if kind:
            sql += " WHERE kind=?"
            args = (kind,)
        sql += " ORDER BY kind, type_id"
        return [self._type_from_row(r) for r in self._fetchall(sql, args)]

    @staticmethod
    def _type_from_row(row: sqlite3.Row) -> TypeDefinition:
        return TypeDefinition(
            type_id=row["type_id"], kind=row["kind"], display_name=row["display_name"],
            schema_version=int(row["schema_version"] or 1), base_type=row["base_type"],
            attributes=_loads(row["attributes"], {}),
            threat_profile=_loads(row["threat_profile"], []),
            analysis_version=int(row["analysis_version"] or 1), status=row["status"],
            description=row["description"], created_at=row["created_at"],
            updated_at=row["updated_at"], version=int(row["version"] or 1),
            proposal_reason=row["proposal_reason"],
        )

    def record_change(self, engagement_id: str, *, operation: str, object_type: str,
                      object_id: str, before: Optional[dict], after: Optional[dict],
                      reason: str = "", actor: str = "plugin") -> ModelChange:
        ch = ModelChange(
            id=new_id("chg"), engagement_id=engagement_id, operation=operation,
            object_type=object_type, object_id=object_id, before=before,
            after=after, reason=reason, actor=actor, created_at=now_iso(),
        )
        self.ensure_engagement(engagement_id)
        self._execute(
            "INSERT INTO model_changes(id,engagement_id,operation,object_type,object_id,before,after,reason,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ch.id, ch.engagement_id, ch.operation, ch.object_type, ch.object_id,
             _dumps(ch.before), _dumps(ch.after), ch.reason, ch.actor, ch.created_at),
        )
        self.bump_model_version(engagement_id)
        return ch

    def list_changes(self, engagement_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM model_changes WHERE engagement_id=? ORDER BY created_at DESC LIMIT ?",
            (engagement_id, limit),
        )
        out = []
        for r in rows:
            out.append({
                "id": r["id"], "operation": r["operation"],
                "object_type": r["object_type"], "object_id": r["object_id"],
                "before": _loads(r["before"], None), "after": _loads(r["after"], None),
                "reason": r["reason"], "actor": r["actor"], "created_at": r["created_at"],
            })
        return out

    def record_analysis_run(self, engagement_id: str, *, analyzer: str,
                            target_refs: list[str], status: str,
                            output: Optional[dict] = None,
                            started_at: str = "", finished_at: str = "") -> None:
        self._execute(
            "INSERT INTO model_analysis_runs(id,engagement_id,analyzer,target_refs,status,output,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?)",
            (new_id("run"), engagement_id, analyzer, _dumps(target_refs), status,
             _dumps(output or {}), started_at or now_iso(), finished_at or now_iso()),
        )

    def snapshot(self, engagement_id: str) -> dict[str, Any]:
        return {
            "engagement_id": engagement_id,
            "model_version": self.get_model_version(engagement_id),
            "nodes": [n.to_dict() for n in self.list_nodes(engagement_id)],
            "relations": [r.to_dict() for r in self.list_relations(engagement_id)],
            "evidence": [e.to_dict() for e in self.list_evidence(engagement_id)],
            "hypotheses": [h.to_dict() for h in self.list_hypotheses(engagement_id)],
            "unknowns": [u.to_dict() for u in self.list_unknowns(engagement_id)],
            "tasks": [t.to_dict() for t in self.list_tasks(engagement_id)],
            "types": [t.to_dict() for t in self.list_types()],
            "changes": self.list_changes(engagement_id, limit=100),
        }
