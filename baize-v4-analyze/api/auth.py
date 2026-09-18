"""Baize 认证管理（独立实现）。

启动时自动生成默认管理员凭证（用户名 + 随机密码 + Token），
并在启动日志中输出。安全策略：

- 密码哈希：PBKDF2-HMAC-SHA256（200k 迭代），每用户独立随机盐，盐随用户持久化；
- 凭证轮换：默认仅在首次启动（或库中没有 admin 用户）时生成并打印凭证，
  之后重启沿用已有用户与 Token；设置环境变量 BAIZE_AUTH_RESET_ON_BOOT=1
  可恢复旧行为（每次重启重置 admin 凭证）；
- Token：支持过期时间（BAIZE_TOKEN_TTL_HOURS，默认 168 小时），
  会话不落盘，服务重启后需重新登录；
- 登录凭证只允许从请求头读取，不再支持 URL 参数透传。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from baize.config import AUTH_DB_FILE

_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    salt: str
    created_at: str


@dataclass
class Session:
    token: str
    user_id: str
    created_at: str
    expires_at: str


def _hash_password(password: str, salt: str) -> str:
    """PBKDF2-HMAC-SHA256，输出 hex。"""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        _PBKDF2_ITERATIONS,
    ).hex()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_at(ttl_hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()


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
        # 每次启动是否重置 admin 凭证（默认沿用已存在用户）
        self._reset_on_boot = os.getenv("BAIZE_AUTH_RESET_ON_BOOT", "").lower() in (
            "1",
            "true",
            "yes",
        )
        self._token_ttl_hours = _parse_ttl(os.getenv("BAIZE_TOKEN_TTL_HOURS", "168"))
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
                # 容错：字段缺失或为旧格式（无 salt）的记录无法校验，直接丢弃
                if not isinstance(u, dict) or not u.get("salt"):
                    continue
                self._users[u["id"]] = User(
                    id=u["id"],
                    username=u.get("username", ""),
                    password_hash=u.get("password_hash", ""),
                    salt=u.get("salt", ""),
                    created_at=u.get("created_at", ""),
                )
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    def _save(self) -> None:
        self._db_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "password_hash": u.password_hash,
                    "salt": u.salt,
                    "created_at": u.created_at,
                }
                for u in self._users.values()
            ]
        }
        tmp = self._db_file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(self._db_file)

    # ------------------------------------------------------------------
    # 默认用户
    # ------------------------------------------------------------------
    def _ensure_default_user(self) -> None:
        """确保存在 admin 用户；仅在首次启动或重置模式下重新生成凭证。"""
        with self._lock:
            existing = next(
                (u for u in self._users.values() if u.username == "admin"), None
            )
            if existing is not None and not self._reset_on_boot:
                return

            # 重置模式：移除旧 admin 并清空会话
            for uid in [u.id for u in self._users.values() if u.username == "admin"]:
                del self._users[uid]
            self._sessions.clear()

            username = "admin"
            password = secrets.token_urlsafe(16)
            token = secrets.token_urlsafe(32)
            salt = secrets.token_hex(_SALT_BYTES)
            user = User(
                id=secrets.token_hex(8),
                username=username,
                password_hash=_hash_password(password, salt),
                salt=salt,
                created_at=_now(),
            )
            self._users[user.id] = user
            self.default_password = password
            self.default_token = token
            self._sessions[token] = Session(
                token=token,
                user_id=user.id,
                created_at=_now(),
                expires_at=_expires_at(self._token_ttl_hours),
            )
            self._save()

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    def validate_token(self, token: str) -> bool:
        with self._lock:
            sess = self._sessions.get(token)
            if sess is None:
                return False
            try:
                expires = datetime.fromisoformat(sess.expires_at)
            except (ValueError, TypeError):
                expires = None
            if expires is not None and expires <= datetime.now(timezone.utc):
                # Token 已过期：清除并拒绝
                self._sessions.pop(token, None)
                return False
            return True

    def validate_password(self, username: str, password: str) -> bool:
        for u in self._users.values():
            if u.username == username:
                return hmac.compare_digest(
                    _hash_password(password, u.salt), u.password_hash
                )
        return False

    def issue_token(self, username: str, password: str) -> str | None:
        if not self.validate_password(username, password):
            return None
        token = secrets.token_urlsafe(32)
        user = next(u for u in self._users.values() if u.username == username)
        self._sessions[token] = Session(
            token=token,
            user_id=user.id,
            created_at=_now(),
            expires_at=_expires_at(self._token_ttl_hours),
        )
        return token


def _parse_ttl(raw: str) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 168.0
    if val <= 0:
        return 168.0
    return val
