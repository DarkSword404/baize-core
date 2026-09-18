"""Baize 会话管理（独立实现）。

管理对话会话及其消息历史，支持创建、读取、删除。
会话数据持久化到 ``~/.baize/sessions/``。
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from baize.config import DEFAULT_BAIZE_DIR
from baize.pentest.blackboard import Blackboard

logger = logging.getLogger("baize.api.sessions")

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
    pattern: Optional[str] = None
    browser_collab: bool = False
    messages: list[dict] = field(default_factory=list)
    # 协作模式（黑板驱动）：目标范围 + 成功条件
    scope: str = ""
    goal: str = ""
    # 任务类型（用户创建时选择）：general | pentest | ctf | forensics
    # 非空时 orchestrator 直接采用，跳过 LLM 自动分类
    task_type: str = ""
    # 运行时黑板，不在 __init__ 签名里（由 SessionManager 注入或恢复）
    blackboard: Optional[Blackboard] = field(default=None, repr=False)
    # 任务-容器解耦：容器绑定 + 任务生命周期
    # container_id = baize-sandbox-{sid}，None=本地运行
    container_id: Optional[str] = None
    container_bound_at: Optional[str] = None
    # active | archived（archived 表示已结束并归档到 ~/.baize/archives/）
    status: str = "active"
    archived_at: Optional[str] = None

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
            "pattern": self.pattern,
            "browser_collab": self.browser_collab,
            "scope": self.scope,
            "goal": self.goal,
            "task_type": self.task_type,
            "blackboard": self.blackboard.snapshot() if self.blackboard else None,
            "container_id": self.container_id,
            "container_bound_at": self.container_bound_at,
            "status": self.status,
            "archived_at": self.archived_at,
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
        # 黑板去抖落盘定时器（session_id -> Timer）
        self._bb_timers: dict[str, threading.Timer] = {}
        self._load_all()

    # ---- 黑板运行期持久化 -------------------------------------------------
    def _bind_blackboard_autosave(self, session_id: str, blackboard: Blackboard) -> None:
        """给黑板注入去抖落盘回调（SSE 流执行期间证据图实时持久化）。"""
        if blackboard is not None and blackboard.on_change is None:
            blackboard.on_change = (
                lambda sid=session_id: self.schedule_blackboard_save(sid)
            )

    def schedule_blackboard_save(self, session_id: str, delay: float = 3.0) -> None:
        """黑板变更后的去抖落盘（delay 秒内多次变更合并为一次写盘）。"""
        with self._lock:
            old = self._bb_timers.pop(session_id, None)
            if old is not None:
                old.cancel()
            timer = threading.Timer(delay, self._flush_blackboard, args=(session_id,))
            timer.daemon = True
            self._bb_timers[session_id] = timer
        timer.start()

    def _flush_blackboard(self, session_id: str) -> None:
        with self._lock:
            self._bb_timers.pop(session_id, None)
            session = self._sessions.get(session_id)
            if session is None:
                return
            try:
                self._save(session)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "黑板运行期落盘失败 session=%s", session_id, exc_info=True
                )

    def attach_blackboard(self, session_id: str, blackboard: Blackboard) -> None:
        """把运行时创建的黑板纳入会话管理（挂自动落盘回调）。"""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.blackboard = blackboard
        self._bind_blackboard_autosave(session_id, blackboard)

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
                    pattern=data.get("pattern"),
                    browser_collab=data.get("browser_collab", False),
                    messages=data.get("messages", []),
                    scope=data.get("scope", ""),
                    goal=data.get("goal", ""),
                    task_type=data.get("task_type", ""),
                    container_id=data.get("container_id"),
                    container_bound_at=data.get("container_bound_at"),
                    status=data.get("status", "active"),
                    archived_at=data.get("archived_at"),
                )
                # 恢复黑板（如有）
                bb_data = data.get("blackboard")
                if bb_data:
                    session.blackboard = Blackboard.from_dict(bb_data)
                    self._bind_blackboard_autosave(session.id, session.blackboard)
                self._sessions[session.id] = session
            except (json.JSONDecodeError, OSError, KeyError) as exc:
                # 损坏的 session 文件不再静默跳过：记录日志并隔离到 .broken/，
                # 避免用户历史任务"凭空消失"且无从排查。
                logger.error("会话文件损坏，已隔离: %s (%s)", f.name, exc)
                broken_dir = self._dir / ".broken"
                try:
                    broken_dir.mkdir(exist_ok=True)
                    f.rename(broken_dir / f.name)
                except OSError:
                    pass

    def _save(self, session: Session) -> None:
        payload = {
            "id": session.id,
            "agent": session.agent,
            "model": session.model,
            "stateful": session.stateful,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "pattern": session.pattern,
            "browser_collab": session.browser_collab,
            "messages": session.messages,
            "scope": session.scope,
            "goal": session.goal,
            "task_type": session.task_type,
            "blackboard": session.blackboard.to_dict() if session.blackboard else None,
            "container_id": session.container_id,
            "container_bound_at": session.container_bound_at,
            "status": session.status,
            "archived_at": session.archived_at,
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
        browser_collab: bool = False,
        scope: str = "",
        goal: str = "",
        task_type: str = "",
    ) -> Session:
        session = Session(
            id=secrets.token_hex(12),
            agent=agent,
            model=model,
            stateful=stateful,
            created_at=_now(),
            updated_at=_now(),
            pattern=pattern,
            browser_collab=browser_collab,
            scope=scope,
            goal=goal,
            task_type=task_type,
        )
        # 协作模式：有 scope/goal 即初始化黑板（agent 可留空，由黑板动态派发）
        if scope or goal:
            session.blackboard = Blackboard(
                session_id=session.id, scope=scope, goal=goal,
            )
        self._bind_blackboard_autosave(session.id, session.blackboard)
        with self._lock:
            self._sessions[session.id] = session
            self._save(session)
        # 任务-容器解耦：创建任务时不再自动启动容器。
        # 容器由用户在任务界面或容器管理页面主动绑定（POST /sessions/{id}/bind-container）。
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[Session]:
        # 任务管理页只列出 active 任务；archived 已移到 ~/.baize/archives/
        with self._lock:
            items = [s for s in self._sessions.values() if s.status == "active"]
        return sorted(items, key=lambda s: s.updated_at, reverse=True)

    # ---- 任务-容器绑定 / 归档 / 恢复 -----------------------------------
    async def bind_container(
        self,
        session_id: str,
        mgr,
        registry,
    ) -> str:
        """为任务异步绑定容器（≤60s）。成功返回容器名。

        - 任务不存在 → KeyError
        - 已绑定 → registry.AlreadyBound（调用方返回 409）
        - 超并发上限 → registry.ContainerLimitExceeded（409）
        - 容器创建失败 → ContainerRuntimeError（503，session 不受影响）
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            if session.status != "active":
                raise RuntimeError(f"session {session_id} not active: {session.status}")
        # 等待容器创建（≤60s）；预检查并发上限，避免无谓创建
        from baize.pentest.container_registry import ContainerRegistry
        if registry.count_active() >= registry.max_concurrency:
            raise ContainerRegistry.ContainerLimitExceeded(
                f"已达容器并发上限 {registry.max_concurrency}"
            )
        if registry.is_bound(session_id):
            raise ContainerRegistry.AlreadyBound(f"session {session_id} 已绑定容器")
        # 真正创建容器
        name = await mgr.ensure_container(session_id)
        # 落地绑定关系（registry.bind 内部再次检查上限，线程安全）
        from baize.pentest.container_runtime import get_image
        try:
            registry.bind(
                session_id=session_id,
                container_name=name,
                runtime=mgr.runtime or "",
                image=get_image(),
            )
        except (ContainerRegistry.AlreadyBound, ContainerRegistry.ContainerLimitExceeded):
            # 并发竞争：刚创建的容器需清理
            await mgr.stop(session_id)
            raise
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.container_id = name
                session.container_bound_at = _now()
                self._save(session)
        logger.info("任务绑定容器 session=%s container=%s", session_id, name)
        return name

    async def unbind_container(self, session_id: str, mgr, registry) -> bool:
        """解除容器绑定：停止容器 + 注册表 unbind + 清 session.container_id。"""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            if not session.container_id:
                return False
        # 停止 + 移除容器
        await mgr.stop(session_id)
        registry.unbind(session_id)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.container_id = None
                session.container_bound_at = None
                self._save(session)
        logger.info("任务解绑容器 session=%s", session_id)
        return True

    def archive_session(self, session_id: str, mgr, registry, archive_manager) -> str:
        """结束任务：容器解绑回池（保留） + 归档 JSON + 内存移除。返回 archived_at。"""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
        # 1) 容器解绑回池（保留容器供其他任务复用，不停止/删除）
        if session.container_id:
            try:
                registry.unbind_keep(session_id)
            except Exception:  # noqa: BLE001
                logger.debug("归档时 unbind_keep 失败 session=%s", session_id, exc_info=True)
            session.container_id = None
            session.container_bound_at = None
        # 2) 文件层归档：标记 status=archived + 移到 archives/
        json_file = self._dir / f"{session_id}.json"
        target = archive_manager.archive(json_file)
        # 3) 内存移除
        with self._lock:
            self._sessions.pop(session_id, None)
        logger.info("任务已归档 session=%s -> %s", session_id, target)
        # 读取归档后的 archived_at
        import json as _json
        try:
            data = _json.loads(target.read_text(encoding="utf-8"))
            return data.get("archived_at", _now())
        except (OSError, _json.JSONDecodeError):
            return _now()

    def restore_session(self, session_id: str, archive_manager) -> Session:
        """从归档恢复为任务：移回 sessions/ + 载入内存。返回恢复后的 Session。"""
        # 文件层恢复：移回 sessions/ + 重置 status=active
        archive_manager.restore(session_id, self._dir)
        # 重新载入内存（参考 _load_all 的单文件加载逻辑）
        f = self._dir / f"{session_id}.json"
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            session = Session(
                id=data["id"],
                agent=data.get("agent"),
                model=data.get("model"),
                stateful=data.get("stateful", True),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                pattern=data.get("pattern"),
                browser_collab=data.get("browser_collab", False),
                messages=data.get("messages", []),
                scope=data.get("scope", ""),
                goal=data.get("goal", ""),
                task_type=data.get("task_type", ""),
                container_id=data.get("container_id"),
                container_bound_at=data.get("container_bound_at"),
                status=data.get("status", "active"),
                archived_at=data.get("archived_at"),
            )
            bb_data = data.get("blackboard")
            if bb_data:
                session.blackboard = Blackboard.from_dict(bb_data)
                self._bind_blackboard_autosave(session.id, session.blackboard)
            with self._lock:
                self._sessions[session.id] = session
            logger.info("任务已恢复 session=%s", session_id)
            return session
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            raise RuntimeError(f"restore session {session_id} failed: {exc}") from exc

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

    def save_assistant_draft(
        self,
        session_id: str,
        content: str,
        reasoning_trace: str = "",
        *,
        finished: bool = False,
    ) -> Session | None:
        """增量保存/定稿 assistant 回复草稿。

        流式执行期间反复调用：同一条草稿消息被原地更新，保证任务中断、
        客户端断连或服务异常时，已产出的正文与工具轨迹不会全部丢失
        （旧实现只在流正常结束时落盘，中断后前端只剩用户消息）。

        - 首次调用创建 ``draft=True`` 的 assistant 消息；
        - finished=True 时去掉 draft 标志（正式回复定稿）。
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            draft: Optional[dict] = None
            for m in reversed(session.messages):
                if m.get("role") == "assistant" and m.get("draft"):
                    draft = m
                    break
                # 只回看本轮（遇到上一条正式消息就停止）
                if m.get("role") in ("user", "assistant"):
                    break
            if draft is None:
                draft = {
                    "role": "assistant",
                    "content": "",
                    "timestamp": _now(),
                    "draft": True,
                }
                session.messages.append(draft)
            draft["content"] = content or ""
            # 工具/思考轨迹可能很长，只保留尾部窗口供回放排障
            if reasoning_trace:
                draft["reasoning_trace"] = reasoning_trace[-8000:]
            if finished:
                draft.pop("draft", None)
            session.updated_at = _now()
            self._save(session)
            return session

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

    def set_browser_collab(self, session_id: str, enabled: bool) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.browser_collab = bool(enabled)
            session.updated_at = _now()
            self._save(session)
            return True
