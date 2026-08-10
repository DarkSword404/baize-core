"""Baize 会话管理（独立实现）。

管理对话会话及其消息历史，支持创建、读取、删除。
会话数据持久化到 ``~/.baize/sessions/``。
"""

from __future__ import annotations

import json
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from baize.config import DEFAULT_BAIZE_DIR

SESSION_DIR = DEFAULT_BAIZE_DIR / "sessions"


@dataclass
class SessionMessage:
    role: str
    content: str
    timestamp: str
    # 供后续扩展：token 用量、工具调用等


@dataclass
class Session:
    id: str
    agent: Optional[str]
    model: Optional[str]
    stateful: bool
    created_at: str
    updated_at: str
    messages: list[dict] = field(default_factory=list)

    @property
    def history_length(self) -> int:
        return len(self.messages)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent": self.agent,
            "model": self.model,
            "stateful": self.stateful,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "history_length": self.history_length,
            "history": self.messages,
            "metadata": {},
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionManager:
    """会话的创建、读取、持久化。"""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or SESSION_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self._dir.exists():
            return
        for f in self._dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                session = Session(
                    id=data["id"],
                    agent=data.get("agent"),
                    model=data.get("model"),
                    stateful=data.get("stateful", True),
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                    messages=data.get("messages", []),
                )
                self._sessions[session.id] = session
            except (json.JSONDecodeError, OSError, KeyError):
                continue

    def _save(self, session: Session) -> None:
        payload = {
            "id": session.id,
            "agent": session.agent,
            "model": session.model,
            "stateful": session.stateful,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "messages": session.messages,
        }
        f = self._dir / f"{session.id}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)

    def create_session(
        self,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        stateful: bool = True,
        pattern: Optional[str] = None,
    ) -> Session:
        session = Session(
            id=secrets.token_hex(12),
            agent=agent,
            model=model,
            stateful=stateful,
            created_at=_now(),
            updated_at=_now(),
        )
        if pattern:
            session.agent = pattern
        with self._lock:
            self._sessions[session.id] = session
            self._save(session)
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[Session]:
        return sorted(
            self._sessions.values(), key=lambda s: s.updated_at, reverse=True
        )

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return False
            f = self._dir / f"{session_id}.json"
            if f.exists():
                f.unlink()
            return True

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        extra: Optional[dict] = None,
    ) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            msg: dict = {
                "role": role,
                "content": content,
                "timestamp": _now(),
            }
            if extra:
                msg.update(extra)
            session.messages.append(msg)
            session.updated_at = _now()
            self._save(session)
            return session

    def get_messages(self, session_id: str) -> list[dict]:
        session = self._sessions.get(session_id)
        return session.messages if session else []

    def reset_messages(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.messages = []
            session.updated_at = _now()
            self._save(session)
            return True

    def set_model(self, session_id: str, model: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.model = model or None
            session.updated_at = _now()
            self._save(session)
            return True
