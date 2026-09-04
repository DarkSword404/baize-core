"""SQLite 执行记录存储（参考 Apache Maka 的 durable execution record）。

作为 SessionLog (JSONL) 的互补存储，提供：
- 结构化 SQL 查询：按会话/工具/时间范围检索执行记录。
- 高效聚合统计：工具调用次数、耗时分布、成功率。
- 崩溃恢复：断点重放，从记录中重建模型历史。
- 零额外依赖：使用 Python 标准库 sqlite3。

与 JSONL SessionLog 的关系：
- SessionLog 是主存储（append-only 事实源），所有事件仍写入 JSONL。
- SQLite 是镜像索引，从 JSONL 同步，提供查询能力。
- 崩溃恢复时从 SQLite 重建历史（比解析 JSONL 更快）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from baize.sdk.session_log import SessionEvent


# ---- 数据库 Schema ----------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    agent       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'  -- active | completed | interrupted
);

CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER NOT NULL,
    session_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    ts          REAL NOT NULL,
    event_id    TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(session_id, kind);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(session_id, ts);

CREATE TABLE IF NOT EXISTS tool_stats (
    session_id  TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    call_count  INTEGER NOT NULL DEFAULT 0,
    total_duration REAL NOT NULL DEFAULT 0.0,
    error_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, tool_name)
);
"""


@dataclass
class SessionRecord:
    id: str
    agent: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "active"


class DbRecorder:
    """SQLite 执行记录器。

    用法::

        rec = DbRecorder()
        rec.start_session("sess_123", agent="web_pentester")
        rec.record_event(event)  # 从 SessionLog 同步事件
        stats = rec.get_tool_stats("sess_123")
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        import os as _os
        if db_path is None:
            data_dir = _os.environ.get("BAIZE_DATA_DIR", str(Path.home() / ".baize"))
            db_path = Path(data_dir) / "runtime.sqlite"
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executescript(SCHEMA)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    # ---- 会话管理 ------------------------------------------------------------

    def start_session(self, session_id: str, agent: str = "") -> None:
        """记录/激活一个会话。幂等：已存在时只更新 agent 与活动时间，
        保留首次 created_at（多轮对话复用同一会话时不被重置）。"""
        now = time.time()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO sessions(id, agent, created_at, updated_at, status)
                   VALUES(?,?,?,?,'active')
                   ON CONFLICT(id) DO UPDATE SET
                     agent=excluded.agent, updated_at=excluded.updated_at, status='active'""",
                (session_id, agent, now, now),
            )
            conn.commit()

    def end_session(self, session_id: str, status: str = "completed") -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE sessions SET updated_at=?, status=? WHERE id=?",
                (time.time(), status, session_id),
            )
            conn.commit()

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT id, agent, created_at, updated_at, status FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            id=row[0], agent=row[1], created_at=row[2],
            updated_at=row[3], status=row[4],
        )

    def list_sessions(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            if status:
                rows = conn.execute(
                    "SELECT id, agent, created_at, updated_at, status FROM sessions WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, agent, created_at, updated_at, status FROM sessions ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {"id": r[0], "agent": r[1], "created_at": r[2], "updated_at": r[3], "status": r[4]}
            for r in rows
        ]

    # ---- 事件记录 ------------------------------------------------------------

    def record_event(self, session_id: str, event: SessionEvent) -> None:
        payload = json.dumps(event.payload, ensure_ascii=False)
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO events(seq, session_id, kind, payload, ts, event_id) VALUES(?,?,?,?,?,?)",
                (event.seq, session_id, event.kind, payload, event.ts, event.id),
            )
            conn.commit()

    def record_events(self, session_id: str, events: list[SessionEvent]) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executemany(
                "INSERT OR REPLACE INTO events(seq, session_id, kind, payload, ts, event_id) VALUES(?,?,?,?,?,?)",
                [(e.seq, session_id, e.kind, json.dumps(e.payload, ensure_ascii=False), e.ts, e.id) for e in events],
            )
            conn.commit()

    def commit(self) -> None:
        with self._lock:
            self._get_conn().commit()

    # ---- 查询 ----------------------------------------------------------------

    def get_events(self, session_id: str, kind: Optional[str] = None) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            if kind:
                rows = conn.execute(
                    "SELECT seq, kind, payload, ts, event_id FROM events WHERE session_id=? AND kind=? ORDER BY seq",
                    (session_id, kind),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT seq, kind, payload, ts, event_id FROM events WHERE session_id=? ORDER BY seq",
                    (session_id,),
                ).fetchall()
        return [
            {"seq": r[0], "kind": r[1], "payload": json.loads(r[2]), "ts": r[3], "event_id": r[4]}
            for r in rows
        ]

    def get_tool_events(self, session_id: str) -> list[dict]:
        return self.get_events(session_id, kind="tool/result")

    def get_user_messages(self, session_id: str) -> list[str]:
        events = self.get_events(session_id, kind="user/message")
        return [e["payload"].get("content", "") for e in events]

    # ---- 工具统计 ------------------------------------------------------------

    def record_tool_call(self, session_id: str, tool_name: str, duration: float = 0.0, error: bool = False) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO tool_stats(session_id, tool_name, call_count, total_duration, error_count)
                   VALUES(?,?,1,?,?) ON CONFLICT(session_id, tool_name) DO UPDATE SET
                   call_count=call_count+1, total_duration=total_duration+?, error_count=error_count+?""",
                (session_id, tool_name, duration, 1 if error else 0, duration, 1 if error else 0),
            )
            conn.commit()

    def get_tool_stats(self, session_id: str) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT tool_name, call_count, total_duration, error_count FROM tool_stats WHERE session_id=? ORDER BY call_count DESC",
                (session_id,),
            ).fetchall()
        return [
            {"tool": r[0], "calls": r[1], "total_duration": round(r[2], 2), "errors": r[3]}
            for r in rows
        ]

    def get_global_tool_stats(self, limit: int = 20) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT tool_name, SUM(call_count), SUM(total_duration), SUM(error_count) FROM tool_stats GROUP BY tool_name ORDER BY SUM(call_count) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"tool": r[0], "calls": r[1], "total_duration": round(r[2], 2), "errors": r[3]}
            for r in rows
        ]

    # ---- 崩溃恢复：从事件重建模型历史 ----------------------------------------

    def derive_messages(self, session_id: str) -> list[dict]:
        """从 SQLite 事件记录重建模型可见历史（用于崩溃恢复）。"""
        events = self.get_events(session_id)
        messages: list[dict] = []
        tool_map: dict[str, str] = {}

        for ev in events:
            p = ev["payload"]
            if ev["kind"] == "user/message":
                messages.append({"role": "user", "content": p.get("content", "")})
            elif ev["kind"] == "agent/response":
                content = p.get("content") or ""
                tool_calls = p.get("tool_calls")
                if tool_calls:
                    msg = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
                    messages.append(msg)
                    for tc in tool_calls:
                        if isinstance(tc, dict) and tc.get("id"):
                            tool_map[tc["id"]] = tc.get("function", {}).get("name", "?")
                else:
                    messages.append({"role": "assistant", "content": content})
            elif ev["kind"] == "tool/result":
                tc_id = p.get("tool_call_id") or p.get("call_id")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id or "?",
                    "content": p.get("output", ""),
                })
        return messages

    def get_last_position(self, session_id: str) -> int:
        """获取会话最后的事件序号（用于断点续传）。"""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT MAX(seq) FROM events WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return row[0] if row[0] is not None else -1

    # ---- 完整事件同步 --------------------------------------------------------

    def sync_from_session_log(self, session_id: str, log: Any) -> int:
        """从 SessionLog 同步事件到 SQLite（增量同步，只追加新事件）。

        返回同步的事件数。
        """
        last_seq = self.get_last_position(session_id)
        new_events = [e for e in log.events if e.seq > last_seq]
        if new_events:
            self.record_events(session_id, new_events)
            self.commit()
        return len(new_events)