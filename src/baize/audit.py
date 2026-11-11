"""统一审计日志（Audit Log）。

设计目标
--------
- **只追加**：每日一个 JSONL 文件（``<data_dir>/audit/audit-YYYYMMDD.jsonl``），
  同时写入 SQLite 索引（``audit.db``）供查询/分页/导出。
- **可校验**：每条事件带 ``prev_hash`` / ``hash``，形成哈希链，能发现
  非尾部篡改或删除（尾部截断需配合外部归档，见 README）。
- **不阻塞业务**：审计失败只记 warning，绝不让请求失败。
- **默认脱敏**：密钥/密码/Token/Cookie 只记录 SHA-256 指纹；长文本截断。

与 SessionLog 的关系
--------------------
``SessionLog`` 仍是会话的单一事实源（完整工具输出、模型响应）；
本模块接收其**摘要事件**（用户消息、工具调用/结果、会话起止），
再加上管理面 API 操作、登录、模型配置变更等，形成跨会话的统一审计视图。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from baize.config import DEFAULT_BAIZE_DIR

logger = logging.getLogger("baize.audit")

_GENESIS = "GENESIS"
_MAX_STRING = 4000
_MAX_DETAIL_JSON = 16000

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|password|passwd|pwd|token|secret|cookie|authorization|"
    r"set-cookie|private[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)


def fingerprint_secret(value: Any) -> str:
    """返回敏感值的不可逆指纹（只用于关联/比对，不可还原）。"""
    if value is None or value == "":
        return ""
    raw = str(value)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


_URL_CREDENTIALS_RE = re.compile(r"(?i)\b(https?://)([^/\s@]+)@")


def sanitize_url(value: str) -> str:
    """去掉 URL 中的 user:password@ 片段，避免审计日志泄露凭据。"""
    return _URL_CREDENTIALS_RE.sub(r"\1***@", value or "")


def _truncate(value: str, limit: int = _MAX_STRING) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[truncated {len(value) - limit} chars]"


def redact(value: Any, *, _depth: int = 0) -> Any:
    """递归脱敏：敏感键只保留指纹，长字符串截断。"""
    if _depth > 8:
        return "[max-depth]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                if isinstance(val, (str, int)) and str(val):
                    out[str(key)] = fingerprint_secret(val)
                elif val:
                    out[str(key)] = "[REDACTED]"
                else:
                    out[str(key)] = ""
            else:
                out[str(key)] = redact(val, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        # 工具参数/输出经常是 JSON 字符串；先解析再脱敏，避免嵌套密钥漏网。
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")) and len(value) <= _MAX_DETAIL_JSON:
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                return _truncate(
                    _canonical(redact(parsed, _depth=_depth + 1))
                )
        return _truncate(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value


def _canonical(obj: Any) -> str:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def _hash_event(prev_hash: str, core: dict[str, Any]) -> str:
    payload = {"prev_hash": prev_hash, **core}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


_EVENT_COLUMNS = (
    "event_id",
    "ts",
    "actor_type",
    "actor_id",
    "ip",
    "ua",
    "action",
    "resource_type",
    "resource_id",
    "status",
    "status_code",
    "session_id",
    "run_id",
    "detail",
)


class AuditLogger:
    """线程安全的审计事件记录器（JSONL + SQLite 索引）。"""

    def __init__(self, data_dir: Path | str | None = None, enabled: bool = True) -> None:
        self.enabled = enabled
        self.dir = Path(data_dir) if data_dir else Path(DEFAULT_BAIZE_DIR) / "audit"
        self.db_path = self.dir / "audit.db"
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._prev_hash = _GENESIS
        self._retention_anchor = _GENESIS
        if self.enabled:
            self._init()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _init(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id      TEXT NOT NULL UNIQUE,
                ts            REAL NOT NULL,
                actor_type    TEXT NOT NULL DEFAULT '',
                actor_id      TEXT NOT NULL DEFAULT '',
                ip            TEXT NOT NULL DEFAULT '',
                ua            TEXT NOT NULL DEFAULT '',
                action        TEXT NOT NULL,
                resource_type TEXT NOT NULL DEFAULT '',
                resource_id   TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT 'ok',
                status_code   INTEGER NOT NULL DEFAULT 0,
                session_id    TEXT NOT NULL DEFAULT '',
                run_id        TEXT NOT NULL DEFAULT '',
                detail        TEXT NOT NULL DEFAULT '{}',
                prev_hash     TEXT NOT NULL,
                hash          TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action);
            CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_id);
            CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events(session_id);
            CREATE TABLE IF NOT EXISTS audit_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT hash FROM audit_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self._prev_hash = row["hash"] if row else _GENESIS
        anchor = self._conn.execute(
            "SELECT value FROM audit_meta WHERE key = 'retention_anchor_hash'"
        ).fetchone()
        self._retention_anchor = anchor["value"] if anchor else _GENESIS
        for path in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
            try:
                if path.exists():
                    os.chmod(path, 0o600)
            except OSError:
                pass
        # 可选保留策略：默认 0（永久保留）。设置天数后，启动时清理更早的
        # 明文 JSONL 与 SQLite 明细，并在 audit_meta 中保留链锚点，
        # 剩余事件仍可通过 verify() 校验。
        try:
            retention_days = float(os.getenv("BAIZE_AUDIT_RETENTION_DAYS", "0") or 0)
        except ValueError:
            retention_days = 0.0
        if retention_days > 0:
            self.purge_before(time.time() - retention_days * 86400)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def record(
        self,
        action: str,
        *,
        actor_type: str = "system",
        actor_id: str = "",
        ip: str = "",
        ua: str = "",
        resource_type: str = "",
        resource_id: str = "",
        status: str = "ok",
        status_code: int = 0,
        session_id: str = "",
        run_id: str = "",
        detail: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """记录一条审计事件。返回事件字典（即使写入失败也返回）。"""
        ts = time.time()
        event_id = f"evt_{uuid.uuid4().hex[:20]}"
        detail_str = _canonical(redact(detail or {}))
        if len(detail_str) > _MAX_DETAIL_JSON:
            detail_str = _canonical(
                {"truncated": True, "preview": detail_str[:_MAX_DETAIL_JSON]}
            )

        core = {
            "event_id": event_id,
            "ts": ts,
            "actor_type": actor_type[:64],
            "actor_id": actor_id[:256],
            "ip": ip[:128],
            "ua": ua[:512],
            "action": action[:128],
            "resource_type": resource_type[:64],
            "resource_id": resource_id[:256],
            "status": status[:32],
            "status_code": int(status_code or 0),
            "session_id": session_id[:128],
            "run_id": run_id[:128],
            "detail": detail_str,
        }

        event: dict[str, Any] = dict(core)
        event["detail"] = json.loads(detail_str) if detail_str else {}
        event["prev_hash"] = self._prev_hash
        event["hash"] = ""
        if not self.enabled:
            return event

        with self._lock:
            prev_hash = self._prev_hash
            digest = _hash_event(prev_hash, core)
            event["prev_hash"] = prev_hash
            event["hash"] = digest
            try:
                assert self._conn is not None
                self._conn.execute(
                    f"""
                    INSERT INTO audit_events
                    (event_id, ts, actor_type, actor_id, ip, ua, action,
                     resource_type, resource_id, status, status_code,
                     session_id, run_id, detail, prev_hash, hash)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        core["event_id"], core["ts"], core["actor_type"],
                        core["actor_id"], core["ip"], core["ua"], core["action"],
                        core["resource_type"], core["resource_id"], core["status"],
                        core["status_code"], core["session_id"], core["run_id"],
                        core["detail"], prev_hash, digest,
                    ),
                )
                self._conn.commit()
                self._prev_hash = digest
                self._append_jsonl(event)
            except Exception:  # noqa: BLE001
                logger.exception("审计事件写入失败: %s", action)
        return event

    def _append_jsonl(self, event: dict[str, Any]) -> None:
        day = time.strftime("%Y%m%d", time.gmtime(event["ts"]))
        path = self.dir / f"audit-{day}.jsonl"
        is_new = not path.exists()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        if is_new:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def query(
        self,
        *,
        action: str = "",
        actor: str = "",
        resource_type: str = "",
        resource_id: str = "",
        session_id: str = "",
        status: str = "",
        since: float | None = None,
        until: float | None = None,
        search: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"events": [], "total": 0, "limit": limit, "offset": offset}
        where: list[str] = []
        params: list[Any] = []
        if action:
            if action.endswith("*"):
                where.append("action LIKE ?")
                params.append(action[:-1] + "%")
            else:
                where.append("action = ?")
                params.append(action)
        if actor:
            where.append("(actor_id = ? OR actor_type = ?)")
            params.extend([actor, actor])
        if resource_type:
            where.append("resource_type = ?")
            params.append(resource_type)
        if resource_id:
            where.append("resource_id = ?")
            params.append(resource_id)
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        if status:
            where.append("status = ?")
            params.append(status)
        if since is not None:
            where.append("ts >= ?")
            params.append(since)
        if until is not None:
            where.append("ts <= ?")
            params.append(until)
        if search:
            where.append("(action LIKE ? OR resource_id LIKE ? OR actor_id LIKE ? OR detail LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like, like])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        limit = max(1, min(int(limit or 100), 1000))
        offset = max(0, int(offset or 0))
        with self._lock:
            assert self._conn is not None
            total = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM audit_events{clause}", params
            ).fetchone()["n"]
            rows = self._conn.execute(
                f"""
                SELECT event_id, ts, actor_type, actor_id, ip, ua, action,
                       resource_type, resource_id, status, status_code,
                       session_id, run_id, detail, prev_hash, hash
                FROM audit_events{clause}
                ORDER BY id DESC LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.get("detail") or "{}")
            except json.JSONDecodeError:
                item["detail"] = {"raw": item.get("detail", "")}
            events.append(item)
        return {"events": events, "total": total, "limit": limit, "offset": offset}

    def export_rows(self, **filters: Any) -> list[dict[str, Any]]:
        filters.pop("limit", None)
        filters.pop("offset", None)
        result = self.query(limit=1000, offset=0, **filters)
        rows = list(result["events"])
        offset = 1000
        while len(rows) < result["total"]:
            page = self.query(limit=1000, offset=offset, **filters)
            batch = page["events"]
            if not batch:
                break
            rows.extend(batch)
            offset += len(batch)
        return rows

    def export(self, fmt: str = "jsonl", **filters: Any) -> tuple[str, str, str]:
        """返回 (content, media_type, filename)。"""
        rows = self.export_rows(**filters)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(list(_EVENT_COLUMNS) + ["prev_hash", "hash"])
            for row in rows:
                writer.writerow(
                    [
                        row.get(col, "") if col != "detail"
                        else _canonical(row.get("detail", {}))
                        for col in _EVENT_COLUMNS
                    ]
                    + [row.get("prev_hash", ""), row.get("hash", "")]
                )
            return buf.getvalue(), "text/csv; charset=utf-8", f"baize-audit-{stamp}.csv"
        lines = [json.dumps(row, ensure_ascii=False, default=str) for row in rows]
        return (
            "\n".join(lines) + ("\n" if lines else ""),
            "application/x-ndjson; charset=utf-8",
            f"baize-audit-{stamp}.jsonl",
        )

    def close(self) -> None:
        """关闭 SQLite 连接（测试或配置切换时使用）。"""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    logger.warning("关闭审计数据库失败", exc_info=True)
                self._conn = None

    def stats(self) -> dict[str, Any]:
        if not self.enabled:
            return {"total": 0, "by_action": [], "by_status": []}
        with self._lock:
            assert self._conn is not None
            total = self._conn.execute(
                "SELECT COUNT(*) AS n FROM audit_events"
            ).fetchone()["n"]
            by_action = [
                {"action": r["action"], "count": r["n"]}
                for r in self._conn.execute(
                    "SELECT action, COUNT(*) AS n FROM audit_events "
                    "GROUP BY action ORDER BY n DESC LIMIT 50"
                ).fetchall()
            ]
            by_status = [
                {"status": r["status"], "count": r["n"]}
                for r in self._conn.execute(
                    "SELECT status, COUNT(*) AS n FROM audit_events "
                    "GROUP BY status ORDER BY n DESC"
                ).fetchall()
            ]
        return {"total": total, "by_action": by_action, "by_status": by_status}

    # ------------------------------------------------------------------
    # 保留策略
    # ------------------------------------------------------------------
    def purge_before(self, cutoff: float) -> int:
        """删除 cutoff 之前的明细，保留链锚点，剩余事件仍可校验。

        注意：这会释放明文审计数据；调用方应确保已有外部归档。
        """
        if not self.enabled:
            return 0
        with self._lock:
            assert self._conn is not None
            last = self._conn.execute(
                "SELECT hash FROM audit_events WHERE ts < ? ORDER BY id DESC LIMIT 1",
                (cutoff,),
            ).fetchone()
            if last is None:
                return 0
            cursor = self._conn.execute(
                "DELETE FROM audit_events WHERE ts < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            self._conn.execute(
                "INSERT OR REPLACE INTO audit_meta(key, value) "
                "VALUES('retention_anchor_hash', ?)",
                (last["hash"],),
            )
            self._conn.commit()
            self._retention_anchor = last["hash"]
        # 仅删除整天都早于 cutoff 的 JSONL，避免误删同一天内仍需保留的事件
        for path in self.dir.glob("audit-*.jsonl"):
            try:
                day = datetime.strptime(path.stem.split("-", 1)[1], "%Y%m%d").replace(
                    tzinfo=timezone.utc
                )
            except (IndexError, ValueError):
                continue
            if day.timestamp() + 86400 <= cutoff:
                try:
                    path.unlink()
                except OSError:
                    logger.warning("删除过期审计文件失败: %s", path, exc_info=True)
        self.record(
            "audit.purge",
            actor_type="system",
            resource_type="audit",
            resource_id="retention",
            detail={
                "deleted": deleted,
                "cutoff": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
            },
        )
        logger.info("审计保留策略：删除 %d 条早于 %s 的事件", deleted, cutoff)
        return deleted

    # ------------------------------------------------------------------
    # 完整性校验
    # ------------------------------------------------------------------
    def verify(self, limit: int = 0) -> dict[str, Any]:
        """重算哈希链。limit>0 时只校验最近 N 条。"""
        if not self.enabled:
            return {"ok": True, "total": 0, "broken_at": None}
        prev = self._retention_anchor
        with self._lock:
            assert self._conn is not None
            sql = (
                "SELECT id, event_id, ts, actor_type, actor_id, ip, ua, action, "
                "resource_type, resource_id, status, status_code, session_id, "
                "run_id, detail, prev_hash, hash FROM audit_events ORDER BY id ASC"
            )
            if limit and limit > 0:
                # 只校验最近 N 条时，需要先取窗口前一条的 hash 作为链起点，
                # 否则第一条的 prev_hash 必然不等于 GENESIS，会误报篡改。
                sql = (
                    "SELECT * FROM ("
                    + sql.replace("ORDER BY id ASC", "ORDER BY id DESC LIMIT ?")
                    + ") ORDER BY id ASC"
                )
                rows = self._conn.execute(sql, (int(limit),)).fetchall()
                if rows:
                    predecessor = self._conn.execute(
                        "SELECT hash FROM audit_events WHERE id < ? ORDER BY id DESC LIMIT 1",
                        (rows[0]["id"],),
                    ).fetchone()
                    prev = predecessor["hash"] if predecessor else self._retention_anchor
            else:
                rows = self._conn.execute(sql).fetchall()
        for idx, row in enumerate(rows):
            core = {
                "event_id": row["event_id"],
                "ts": row["ts"],
                "actor_type": row["actor_type"],
                "actor_id": row["actor_id"],
                "ip": row["ip"],
                "ua": row["ua"],
                "action": row["action"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "status": row["status"],
                "status_code": row["status_code"],
                "session_id": row["session_id"],
                "run_id": row["run_id"],
                "detail": row["detail"],
            }
            expected = _hash_event(prev, core)
            if row["prev_hash"] != prev or row["hash"] != expected:
                return {
                    "ok": False,
                    "total": len(rows),
                    "broken_at": row["event_id"],
                    "index": idx,
                }
            prev = row["hash"]
        return {"ok": True, "total": len(rows), "broken_at": None}


_audit_logger: Optional[AuditLogger] = None
_audit_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    with _audit_lock:
        if _audit_logger is None:
            enabled = os.getenv("BAIZE_AUDIT_ENABLED", "1").lower() not in (
                "0", "false", "no",
            )
            _audit_logger = AuditLogger(enabled=enabled)
        return _audit_logger


def reset_audit_logger() -> None:
    """测试/配置切换用：关闭并丢弃单例。"""
    global _audit_logger
    with _audit_lock:
        if _audit_logger is not None:
            _audit_logger.close()
        _audit_logger = None
