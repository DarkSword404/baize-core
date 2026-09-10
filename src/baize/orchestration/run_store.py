"""
执行记录持久化存储 — SQLite 实现。

存储每次流水线执行的生命周期记录和事件历史。
服务重启后可通过 list_by_status() 恢复中断的 run。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Literal

JobStatus = Literal["pending", "running", "completed", "failed", "paused"]

_DEFAULT_DB_PATH = Path.home() / ".baize" / "orchestration" / "runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    pipe_type   TEXT NOT NULL,
    status      TEXT NOT NULL,
    context     TEXT NOT NULL DEFAULT '{}',
    webhook     TEXT NOT NULL DEFAULT '',
    dedup_key   TEXT,
    created_at  REAL NOT NULL,
    started_at  REAL,
    ended_at    REAL,
    error       TEXT NOT NULL DEFAULT '',
    nodes       TEXT NOT NULL DEFAULT '{}',
    events      TEXT NOT NULL DEFAULT '[]',
    report      TEXT NOT NULL DEFAULT '',
    dialog      TEXT NOT NULL DEFAULT '[]',
    dialog_action TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_dedup_key
    ON runs(dedup_key) WHERE dedup_key IS NOT NULL AND dedup_key != '';
"""

# 旧库渐进式迁移：给既有 runs 表补充对话相关列（幂等）
_MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN dialog TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE runs ADD COLUMN dialog_action TEXT NOT NULL DEFAULT ''",
]


class RunRecord:
    """一次流水线执行记录。"""

    def __init__(
        self,
        run_id: str,
        pipeline_id: str,
        pipe_type: str,
        status: JobStatus = "pending",
    ):
        self.run_id = run_id
        self.pipeline_id = pipeline_id
        self.pipe_type = pipe_type
        self.status: JobStatus = status
        self.context: dict[str, Any] = {}
        self.webhook: str = ""
        self.dedup_key: str = ""
        self.created_at = time.time()
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.error: str = ""

        # 节点执行记录
        self.nodes: dict[str, dict[str, Any]] = {}

        # 事件历史（供重连时补齐）
        self.events: list[dict[str, Any]] = []

        # 最终输出
        self.report: str = ""

        # 流水线对话（每次入站数据 = 一条对话）：entries 追加自各节点，
        # end 节点可标记 discard/save 决定终态回收
        self.dialog: list[dict[str, Any]] = []
        self.dialog_action: str = ""   # "" | "discard" | "save"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "pipe_type": self.pipe_type,
            "status": self.status,
            "context": self.context,
            "webhook": self.webhook,
            "dedup_key": self.dedup_key,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "nodes": self.nodes,
            "events": self.events,
            "events_count": len(self.events),
            "report": self.report,
            "dialog": self.dialog,
            "dialog_retained": self.dialog_action != "discard" and bool(self.dialog),
            "dialog_action": self.dialog_action,
            "dialog_count": len(self.dialog),
        }

    def brief(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "pipe_type": self.pipe_type,
            "status": self.status,
            "created_at": self.created_at,
            "error": self.error,
        }


class RunStore:
    """运行记录仓库 — SQLite 持久化，线程安全。"""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = str(db_path or os.environ.get("BAIZE_RUNS_DB") or _DEFAULT_DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            # 渐进式迁移（老库补列，幂等）
            for ddl in _MIGRATIONS:
                try:
                    self._conn.execute(ddl)
                    self._conn.commit()
                except sqlite3.OperationalError:
                    pass  # 列已存在

    # ------------------------------------------------------------ 内部工具
    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> RunRecord:
        rec = RunRecord(row["run_id"], row["pipeline_id"], row["pipe_type"], row["status"])
        rec.context = json.loads(row["context"] or "{}")
        rec.webhook = row["webhook"] or ""
        rec.dedup_key = row["dedup_key"] or ""
        rec.created_at = row["created_at"]
        rec.started_at = row["started_at"]
        rec.ended_at = row["ended_at"]
        rec.error = row["error"] or ""
        rec.nodes = json.loads(row["nodes"] or "{}")
        rec.events = json.loads(row["events"] or "[]")
        rec.report = row["report"] or ""
        rec.dialog = json.loads(row["dialog"] or "[]")
        rec.dialog_action = row["dialog_action"] or ""
        return rec

    def _fetch(self, run_id: str) -> RunRecord | None:
        cur = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def _persist(self, rec: RunRecord) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO runs
               (run_id, pipeline_id, pipe_type, status, context, webhook, dedup_key,
                created_at, started_at, ended_at, error, nodes, events, report,
                dialog, dialog_action)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.run_id,
                rec.pipeline_id,
                rec.pipe_type,
                rec.status,
                json.dumps(rec.context, ensure_ascii=False),
                rec.webhook,
                rec.dedup_key or None,
                rec.created_at,
                rec.started_at,
                rec.ended_at,
                rec.error,
                json.dumps(rec.nodes, ensure_ascii=False),
                json.dumps(rec.events, ensure_ascii=False),
                rec.report,
                json.dumps(rec.dialog, ensure_ascii=False),
                rec.dialog_action,
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------ 基础操作
    def create(self, record: RunRecord) -> RunRecord:
        """创建记录；若 dedup_key 已存在则返回既有记录（幂等）。"""
        with self._lock:
            if record.dedup_key:
                existing = self._conn.execute(
                    "SELECT * FROM runs WHERE dedup_key = ?", (record.dedup_key,)
                ).fetchone()
                if existing:
                    return self._row_to_record(existing)
            self._persist(record)
            return record

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._fetch(run_id)

    def update_status(self, run_id: str, status: JobStatus, error: str = "") -> None:
        with self._lock:
            rec = self._fetch(run_id)
            if not rec:
                return
            rec.status = status
            if error:
                rec.error = error
            if status == "running" and rec.started_at is None:
                rec.started_at = time.time()
            if status in ("completed", "failed") and rec.ended_at is None:
                rec.ended_at = time.time()
            self._persist(rec)

    def add_event(self, run_id: str, event: dict[str, Any]) -> None:
        """追加事件到历史列表。"""
        with self._lock:
            rec = self._fetch(run_id)
            if not rec:
                return
            rec.events.append(event)
            self._persist(rec)

    def add_node_record(self, run_id: str, node_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            rec = self._fetch(run_id)
            if not rec:
                return
            rec.nodes[node_id] = record
            self._persist(rec)

    def set_final_state(self, run_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            rec = self._fetch(run_id)
            if not rec:
                return
            final_status = state.get("status", "completed")
            rec.status = final_status
            rec.nodes = state.get("nodes", rec.nodes)
            rec.report = self._extract_report(state)
            rec.error = state.get("error", "")
            dialog = state.get("dialog")
            if isinstance(dialog, list):
                rec.dialog = dialog
            rec.dialog_action = state.get("dialog_action") or rec.dialog_action or ""
            if rec.ended_at is None:
                rec.ended_at = time.time()
            self._persist(rec)

    # --------------------------------------------------- 对话（run 级）操作
    def append_dialog(self, run_id: str, entries: list[dict[str, Any]]) -> None:
        """追加对话条目（通常配合节点落库前调用；亦可用于流式追加）。"""
        if not entries:
            return
        with self._lock:
            rec = self._fetch(run_id)
            if not rec:
                return
            rec.dialog.extend(entries)
            self._persist(rec)

    def set_dialog_action(self, run_id: str, action: str) -> None:
        """标记对话处置动作：discard（回收删除）/ save（保留归档）。"""
        with self._lock:
            rec = self._fetch(run_id)
            if not rec:
                return
            rec.dialog_action = action
            self._persist(rec)

    def discard_dialog(self, run_id: str) -> None:
        """回收对话：清空对话内容，仅保留 run 摘要/节点记录（历史仍可查）。"""
        with self._lock:
            rec = self._fetch(run_id)
            if not rec:
                return
            rec.dialog = []
            rec.dialog_action = "discard"
            self._persist(rec)

    @staticmethod
    def _extract_report(state: dict[str, Any]) -> str:
        """提取最终报告：
        1. 优先顶层 report；
        2. 其次名为 report 的节点输出（含内层 subpipeline 节点）；
        3. 回退到最后一个成功完成的节点输出。
        """
        report = state.get("report", "") or ""
        if report:
            return report
        nodes = state.get("nodes") or {}
        rep = nodes.get("report")
        if isinstance(rep, dict):
            report = rep.get("output", "") or ""
            if not report:
                inner = rep.get("nodes", {})
                if isinstance(inner, dict):
                    report = (inner.get("report") or {}).get("output", "") or ""
            if report:
                return report
        # 回退：取最后一个 status=completed 且含 output 的节点
        for rec in nodes.values():
            if not isinstance(rec, dict):
                continue
            if rec.get("status") == "completed" and rec.get("output"):
                report = rec.get("output", "")
        if not isinstance(report, str):
            report = str(report)
        return report

    def list_runs(
        self,
        pipeline_id: str | None = None,
        status: JobStatus | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出运行记录（按创建时间倒序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit * 4,),
            ).fetchall()
        records = [self._row_to_record(r) for r in rows]
        if pipeline_id:
            records = [r for r in records if r.pipeline_id == pipeline_id]
        if status:
            records = [r for r in records if r.status == status]
        return [r.brief() for r in records[:limit]]

    def get_events_since(self, run_id: str, last_event_id: str = "") -> list[dict[str, Any]]:
        """获取指定事件之后的增量事件（用于重连补齐）。"""
        with self._lock:
            rec = self._fetch(run_id)
            if not rec:
                return []
            if last_event_id:
                try:
                    idx = next(
                        i for i, e in enumerate(rec.events)
                        if e.get("event_id") == last_event_id
                    )
                    return rec.events[idx + 1:]
                except StopIteration:
                    return []
            return rec.events

    # ------------------------------------------------------------ 去重 / 恢复
    def find_by_dedup_key(self, dedup_key: str) -> RunRecord | None:
        """按去重键查找既有 run（幂等提交用）。"""
        if not dedup_key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE dedup_key = ?", (dedup_key,)
            ).fetchone()
            return self._row_to_record(row) if row else None

    def list_by_status(self, statuses: list[str]) -> list[RunRecord]:
        """按状态列表查询（服务重启后恢复中断 run 用）。"""
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM runs WHERE status IN ({placeholders}) ORDER BY created_at",
                statuses,
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


# ====================================================================
# 流水线激活状态管理
# ====================================================================

_ACTIVATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS activations (
    pipeline_id TEXT PRIMARY KEY,
    active      INTEGER NOT NULL DEFAULT 0,
    receiver_id TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL
);
"""


class PipelineActivationStore:
    """自动化流水线激活状态管理（SQLite 持久化）。

    auto 类型的流水线创建后默认关闭，需要用户手动开启后才开始接收数据；
    激活态跨重启保留，服务重启后由 SessionSupervisor.resume_all() 恢复长驻会话。
    manual 类型的流水线始终处于可用状态，供对话时选择。

    receiver_id 记录该流水线绑定的数据接收器（激活时由请求方提供），
    供 Supervisor 会话 claim 收件箱数据。
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = str(db_path or os.environ.get("BAIZE_RUNS_DB") or _DEFAULT_DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_ACTIVATION_SCHEMA)
            self._conn.commit()
        self._activations: dict[str, bool] = {}   # pipeline_id -> is_active
        self._bindings: dict[str, str] = {}       # pipeline_id -> receiver_id
        self._load()

    def _load(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT pipeline_id, active, receiver_id FROM activations"
            ).fetchall()
        for r in rows:
            self._activations[str(r["pipeline_id"])] = bool(r["active"])
            rid = str(r["receiver_id"] or "")
            if rid:
                self._bindings[str(r["pipeline_id"])] = rid

    def _persist(self, pipeline_id: str) -> None:
        # 注意：仅由已持有 self._lock 的公开方法调用（activate/deactivate/
        # set_binding），自身不得再加锁——threading.Lock 不可重入，否则死锁。
        self._conn.execute(
            "INSERT OR REPLACE INTO activations (pipeline_id, active, receiver_id, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (
                pipeline_id,
                1 if self._activations.get(pipeline_id, False) else 0,
                self._bindings.get(pipeline_id, ""),
                time.time(),
            ),
        )
        self._conn.commit()

    def is_active(self, pipeline_id: str) -> bool:
        with self._lock:
            return self._activations.get(pipeline_id, False)

    def activate(self, pipeline_id: str, receiver_id: str = "") -> None:
        with self._lock:
            self._activations[pipeline_id] = True
            if receiver_id:
                self._bindings[pipeline_id] = receiver_id
            self._persist(pipeline_id)

    def deactivate(self, pipeline_id: str) -> None:
        with self._lock:
            self._activations[pipeline_id] = False
            self._persist(pipeline_id)

    def get_all_active(self) -> list[str]:
        with self._lock:
            return [pid for pid, active in self._activations.items() if active]

    def get_binding(self, pipeline_id: str) -> str:
        """返回流水线绑定的接收器 id（可能为空串）。"""
        with self._lock:
            return self._bindings.get(pipeline_id, "")

    def set_binding(self, pipeline_id: str, receiver_id: str) -> None:
        with self._lock:
            self._bindings[pipeline_id] = receiver_id
            self._persist(pipeline_id)

    def get_status(self, pipeline_id: str) -> dict[str, Any]:
        return {
            "pipeline_id": pipeline_id,
            "active": self.is_active(pipeline_id),
            "receiver_id": self.get_binding(pipeline_id),
        }


# ====================================================================
# 全局实例
# ====================================================================

_default_store = RunStore()
_default_activation_store = PipelineActivationStore()


def get_run_store() -> RunStore:
    return _default_store


def get_activation_store() -> PipelineActivationStore:
    return _default_activation_store
