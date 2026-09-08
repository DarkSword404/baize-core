"""
Alert Inbox — 持久化告警收件箱。

定位：告警数据的"唯一事实源"（对应 ALERT-TRIAGE-SILENT-SESSION.md §4.1）。

Webhook / Syslog / File watcher 等入站数据先落此表（而非仅内存队列），
由编排侧的长驻 Supervisor（baize.orchestration.session）claim 后逐条执行研判。

语义：
- 指纹幂等：同一接收器下的活跃告警（queued/processing/failed）按 fingerprint 唯一，
  重复投递直接返回既有 seq（不重复处理）。
- lease：claim 时置 processing 并带 lease 到期时间；进程崩溃后 requeue_expired()
  会把超时租约回收为 queued（at-least-once，最终由 runs.dedup_key 兜底幂等）。
- 失败重试：fail() 递增 attempts 并设置 backoff（next_attempt_at），超限置 dead。
- done / dead 不参与指纹唯一约束，同一指纹的新告警可再次入队（允许状态更新）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = None  # lazy import 避免重名冲突

_ACTIVE_STATUSES = ("queued", "processing", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_inbox (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    receiver_id     TEXT NOT NULL,              -- 来源接收器
    fingerprint     TEXT NOT NULL,              -- 幂等指纹
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued|processing|done|failed|dead
    lease_until     REAL,                       -- processing 租约到期时间
    next_attempt_at REAL NOT NULL DEFAULT 0,    -- 失败重试最早时间
    attempts        INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT '',
    content_type    TEXT NOT NULL DEFAULT '',
    raw_payload     BLOB NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}',
    received_at     REAL NOT NULL,
    run_id          TEXT NOT NULL DEFAULT '',   -- 关联的 run 记录
    error           TEXT NOT NULL DEFAULT '',
    processed_at    REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_active_fp
    ON alert_inbox(receiver_id, fingerprint)
    WHERE status IN ('queued', 'processing', 'failed');
CREATE INDEX IF NOT EXISTS idx_inbox_claim
    ON alert_inbox(status, receiver_id, seq);
"""

_DEFAULT_DB_PATH = Path.home() / ".baize" / "orchestration" / "alerts.db"
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE_SECONDS = 900.0


def compute_fingerprint(
    receiver_id: str,
    metadata: dict[str, Any] | None,
    raw_payload: bytes,
    source: str = "",
) -> str:
    """幂等指纹：优先告警自带 ID，否则对内容哈希。"""
    md = metadata or {}
    alert_id = md.get("alert_id") or md.get("id") or md.get("event_id")
    if alert_id:
        return f"id:{alert_id}"
    h = hashlib.sha256()
    h.update(str(receiver_id).encode("utf-8"))
    h.update(b"\x00")
    h.update(raw_payload or b"")
    h.update(b"\x00")
    h.update(str(source or md.get("source") or "").encode("utf-8"))
    return "sha256:" + h.hexdigest()


class AlertInbox:
    """收件箱 — SQLite 持久化，线程安全（同一进程内）。"""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = str(db_path or os.environ.get("BAIZE_ALERTS_DB") or _DEFAULT_DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------- 内部工具
    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None, with_payload: bool = True) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        raw = d.get("raw_payload")
        d["payload_size"] = len(raw) if raw is not None else 0
        if not with_payload:
            d.pop("raw_payload", None)
        return d

    def _fetch(self, seq: int) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM alert_inbox WHERE seq = ?", (seq,)
        )
        return cur.fetchone()

    # ------------------------------------------------------------- 入站
    def enqueue(
        self,
        receiver_id: str,
        raw_payload: bytes,
        content_type: str = "",
        source: str = "",
        metadata: dict[str, Any] | None = None,
        received_at: float | None = None,
        fingerprint: str = "",
    ) -> tuple[int, bool]:
        """写入收件箱。

        Returns:
            (seq, created): created=False 表示同指纹的活跃告警已存在（幂等丢弃）。
        """
        ts = received_at if received_at is not None else time.time()
        fp = fingerprint or compute_fingerprint(receiver_id, metadata, raw_payload, source)
        md_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock:
            row = self._conn.execute(
                "SELECT seq FROM alert_inbox WHERE receiver_id = ? AND fingerprint = ?"
                " AND status IN ('queued', 'processing', 'failed')",
                (receiver_id, fp),
            ).fetchone()
            if row:
                return int(row["seq"]), False
            cur = self._conn.execute(
                "INSERT INTO alert_inbox"
                " (receiver_id, fingerprint, source, content_type, raw_payload,"
                "  metadata, received_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (receiver_id, fp, source, content_type, raw_payload, md_json, ts),
            )
            self._conn.commit()
            return int(cur.lastrowid), True

    # ------------------------------------------------------------- 消费（Supervisor）
    def claim_next(
        self,
        receiver_id: str,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any] | None:
        """原子抢取下一条待处理告警（queued 或到达重试时间的 failed）。

        先回收本接收器的过期租约，再取队首。
        """
        now = time.time()
        with self._lock:
            # 1. 过期租约回收（崩溃恢复）
            self._conn.execute(
                "UPDATE alert_inbox SET status = 'queued', lease_until = NULL"
                " WHERE receiver_id = ? AND status = 'processing' AND lease_until IS NOT NULL"
                " AND lease_until < ?",
                (receiver_id, now),
            )
            # 2. 取队首（含 failed 到点重试项）
            row = self._conn.execute(
                "SELECT seq FROM alert_inbox"
                " WHERE receiver_id = ? AND status = 'queued' AND next_attempt_at <= ?"
                " ORDER BY seq LIMIT 1",
                (receiver_id, now),
            ).fetchone()
            if row is None:
                self._conn.commit()
                return None
            seq = int(row["seq"])
            self._conn.execute(
                "UPDATE alert_inbox SET status = 'processing', lease_until = ?, attempts = attempts + 1"
                " WHERE seq = ? AND status = 'queued'",
                (now + lease_seconds, seq),
            )
            fetched = self._conn.execute(
                "SELECT * FROM alert_inbox WHERE seq = ?", (seq,)
            ).fetchone()
            self._conn.commit()
            return self._row_to_dict(fetched)

    def complete(self, seq: int, run_id: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE alert_inbox SET status = 'done', run_id = ?,"
                " error = '', processed_at = ?, lease_until = NULL WHERE seq = ?",
                (run_id, time.time(), seq),
            )
            self._conn.commit()

    def fail(self, seq: int, error: str = "", max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> str:
        """标记失败；重试未超限则 backoff 后回 queued，否则置 dead。

        Returns: 当前 attempts 或 'dead' 标记。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT attempts FROM alert_inbox WHERE seq = ?", (seq,)
            ).fetchone()
            attempts = int(row["attempts"]) if row else 0
            if attempts >= max_attempts:
                self._conn.execute(
                    "UPDATE alert_inbox SET status = 'dead', error = ?,"
                    " lease_until = NULL, processed_at = ? WHERE seq = ?",
                    (error, time.time(), seq),
                )
                self._conn.commit()
                return "dead"
            # backoff: 10s, 60s, 300s, 600s（按 attempt 次数）
            backoff = min(10 * (6 ** max(0, attempts - 1)), 600)
            self._conn.execute(
                "UPDATE alert_inbox SET status = 'queued', next_attempt_at = ?,"
                " error = ?, lease_until = NULL WHERE seq = ?",
                (time.time() + backoff, error, seq),
            )
            self._conn.commit()
            return "retry"

    # ------------------------------------------------------------- 管理/查询
    def requeue_expired(self, receiver_id: str = "") -> int:
        """回收所有过期租约（进程崩溃恢复），返回回收数量。"""
        now = time.time()
        with self._lock:
            if receiver_id:
                cur = self._conn.execute(
                    "UPDATE alert_inbox SET status = 'queued', lease_until = NULL"
                    " WHERE status = 'processing' AND lease_until IS NOT NULL"
                    " AND lease_until < ? AND receiver_id = ?",
                    (now, receiver_id),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE alert_inbox SET status = 'queued', lease_until = NULL"
                    " WHERE status = 'processing' AND lease_until IS NOT NULL"
                    " AND lease_until < ?",
                    (now,),
                )
            self._conn.commit()
            return cur.rowcount

    def stats(self, receiver_id: str = "") -> dict[str, Any]:
        """按状态统计 + 积压深度。"""
        with self._lock:
            if receiver_id:
                rows = self._conn.execute(
                    "SELECT status, COUNT(*) AS n FROM alert_inbox"
                    " WHERE receiver_id = ? GROUP BY status",
                    (receiver_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT status, COUNT(*) AS n FROM alert_inbox GROUP BY status"
                ).fetchall()
        counts = {r["status"]: int(r["n"]) for r in rows}
        backlog = counts.get("queued", 0)
        return {
            "queued": counts.get("queued", 0),
            "processing": counts.get("processing", 0),
            "done": counts.get("done", 0),
            "failed": counts.get("failed", 0),
            "dead": counts.get("dead", 0),
            "total": sum(counts.values()),
            "backlog": backlog,
        }

    def list_items(
        self,
        receiver_id: str = "",
        status: str = "",
        limit: int = 50,
        offset: int = 0,
        with_payload: bool = False,
    ) -> list[dict[str, Any]]:
        """分页查询收件箱条目（默认不含 payload）。"""
        sql = "SELECT * FROM alert_inbox"
        conds: list[str] = []
        args: list[Any] = []
        if receiver_id:
            conds.append("receiver_id = ?")
            args.append(receiver_id)
        if status:
            conds.append("status = ?")
            args.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY seq LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_dict(r, with_payload=with_payload) for r in rows]

    def get(self, seq: int, with_payload: bool = True) -> dict[str, Any] | None:
        with self._lock:
            return self._row_to_dict(self._fetch(seq), with_payload=with_payload)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


# ====================================================================
# 全局实例
# ====================================================================

_instance: AlertInbox | None = None
_instance_lock = threading.Lock()


def get_alert_inbox() -> AlertInbox:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = AlertInbox()
    return _instance
