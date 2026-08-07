"""Baize 认证管理（独立实现）。

启动时自动生成默认管理员凭证（用户名 + 随机密码 + Token），
并在启动日志中输出。密码与 Token 每次重启自动重新生成。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from baize.config import AUTH_DB_FILE


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    created_at: str


@dataclass
class Session:
    token: str
    user_id: str
    created_at: str


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuthManager:
    """用户与 Token 认证管理。"""

    def __init__(self, db_file: Path | None = None) -> None:
        self._db_file = db_file or AUTH_DB_FILE
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._sessions: dict[str, Session] = {}
        self.default_username = "admin"
        self.default_password: str | None = None
        self.default_token: str | None = None
        self._load()
        self._ensure_default_user()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self._db_file.exists():
            return
        try:
            data = json.loads(self._db_file.read_text(encoding="utf-8"))
            for u in data.get("users", []):
                self._users[u["id"]] = User(**u)
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    def _save(self) -> None:
        self._db_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "password_hash": u.password_hash,
                    "created_at": u.created_at,
                }
                for u in self._users.values()
            ]
        }
        tmp = self._db_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._db_file)

    # ------------------------------------------------------------------
    # 默认用户
    # ------------------------------------------------------------------
    def _ensure_default_user(self) -> None:
        """每次重启重新生成默认管理员凭证。"""
        with self._lock:
            # 移除旧 admin，创建新 admin
            old = [uid for uid, u in self._users.items() if u.username == "admin"]
            for uid in old:
                del self._users[uid]
            self._sessions.clear()

            username = "admin"
            password = secrets.token_urlsafe(16)
            token = secrets.token_urlsafe(32)
            salt = secrets.token_hex(8)
            user = User(
                id=secrets.token_hex(8),
                username=username,
                password_hash=_hash_password(password, salt),
                created_at=_now(),
            )
            # 存 salt 以便校验（简化为 password_hash 内含 salt）
            self._users[user.id] = user
            self.default_password = password
            self.default_token = token
            self._sessions[token] = Session(
                token=token,
                user_id=user.id,
                created_at=_now(),
            )
            self._salt = salt
            self._save()

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    def validate_token(self, token: str) -> bool:
        return token in self._sessions

    def validate_password(self, username: str, password: str) -> bool:
        for u in self._users.values():
            if u.username == username:
                salt = getattr(self, "_salt", "")
                return hmac.compare_digest(
                    _hash_password(password, salt), u.password_hash
                )
        return False

    def issue_token(self, username: str, password: str) -> str | None:
        if not self.validate_password(username, password):
            return None
        token = secrets.token_urlsafe(32)
        user = next(u for u in self._users.values() if u.username == username)
        self._sessions[token] = Session(token=token, user_id=user.id, created_at=_now())
        return token
