"""经验库存储层（ExperienceStore）。

设计目标：
- 零额外依赖：沿用项目统一的 JSON 落盘 + 内存缓存模式（与 sessions 一致）。
- 按作用域分文件：全局经验 `global.json` + 每个智能体专属 `agent_{key}.json`。
- 条目可携带 embedding 向量（由可插拔的 EmbeddingProvider 填充），
  向量维度/模型固化在条目中，切换模型时自动重建。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BAIZE_DIR = Path.home() / ".baize"
EXPERIENCES_DIR = BAIZE_DIR / "experiences"

GLOBAL_SCOPE = "global"
AGENT_PREFIX = "agent:"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return f"exp_{uuid.uuid4().hex[:12]}"


def _sanitize(key: str) -> str:
    """将 agent_key 规范化为安全的文件名片段。"""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)


def scope_file_name(scope: str) -> str:
    if scope == GLOBAL_SCOPE:
        return "global.json"
    if scope.startswith(AGENT_PREFIX):
        return f"agent_{_sanitize(scope[len(AGENT_PREFIX):])}.json"
    return f"scope_{_sanitize(scope)}.json"


@dataclass
class ExperienceItem:
    """一条复盘总结式经验条目。"""

    id: str
    scope: str  # "global" | "agent:{agent_key}"
    title: str
    content: str  # 复盘总结文本（教训 + 可复用步骤 + 适用条件）
    tags: list[str] = field(default_factory=list)
    source_session_id: str = ""
    source_agent: str = ""
    created_at: str = ""
    updated_at: str = ""
    enabled: bool = True
    importance: int = 0  # 用户可标记重要程度（0-5）
    hit_count: int = 0  # 被检索注入次数（评估质量用）
    embedding: Optional[list[float]] = None
    embedding_model: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExperienceItem":
        data = dict(data)
        data["id"] = str(data.get("id") or new_id())
        return cls(**data)


class ExperienceStore:
    """经验库：按作用域分文件持久化，提供 CRUD 与检索所需的基础能力。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or EXPERIENCES_DIR
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[str, list[ExperienceItem]] = {}
        self._loaded: set[str] = set()

    # ------------------------------------------------------------------
    # 文件读写
    # ------------------------------------------------------------------
    def _scope_path(self, scope: str) -> Path:
        return self._base_dir / scope_file_name(scope)

    def _load_scope(self, scope: str) -> list[ExperienceItem]:
        path = self._scope_path(scope)
        if scope not in self._loaded:
            items: list[ExperienceItem] = []
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        items = [ExperienceItem.from_dict(d) for d in raw if isinstance(d, dict)]
                except (json.JSONDecodeError, OSError):
                    items = []
            self._cache[scope] = items
            self._loaded.add(scope)
        return self._cache.get(scope, [])

    def _save_scope(self, scope: str) -> None:
        path = self._scope_path(scope)
        payload = [i.to_dict() for i in self._cache.get(scope, [])]
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _all_scopes(self) -> list[str]:
        return [GLOBAL_SCOPE] + [p.stem.replace("agent_", AGENT_PREFIX, 1)
                                 for p in self._base_dir.glob("agent_*.json")]

    def _normalize_scope(self, scope: str) -> str:
        return scope if scope in (GLOBAL_SCOPE, *[f"{AGENT_PREFIX}{a}" for a in self.agent_keys()]) else GLOBAL_SCOPE

    def agent_keys(self) -> list[str]:
        return [p.stem[len("agent_"):] for p in self._base_dir.glob("agent_*.json")]

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_items(
        self,
        scope: str | None = None,
        agent_key: str | None = None,
        include_disabled: bool = True,
    ) -> list[ExperienceItem]:
        """按作用域/智能体过滤列出条目。"""
        result: list[ExperienceItem] = []
        if scope:
            result.extend(self._load_scope(scope))
        elif agent_key:
            result.extend(self._load_scope(f"{AGENT_PREFIX}{_sanitize(agent_key)}"))
        else:
            for s in self._all_scopes():
                result.extend(self._load_scope(s))
        if not include_disabled:
            result = [i for i in result if i.enabled]
        result.sort(key=lambda i: (i.enabled, i.importance, i.updated_at), reverse=True)
        return result

    def get_item(self, item_id: str) -> ExperienceItem | None:
        for s in self._all_scopes():
            for item in self._load_scope(s):
                if item.id == item_id:
                    return item
        return None

    def get_by_source_session(self, session_id: str) -> list[ExperienceItem]:
        return [i for s in self._all_scopes() for i in self._load_scope(s)
                if i.source_session_id == session_id]

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def create(self, data: dict) -> ExperienceItem:
        item = ExperienceItem.from_dict(data)
        if item.scope != GLOBAL_SCOPE and not item.scope.startswith(AGENT_PREFIX):
            item.scope = GLOBAL_SCOPE
        with self._lock:
            items = self._load_scope(item.scope)
            items.append(item)
            self._save_scope(item.scope)
        return item

    def update(self, item_id: str, data: dict) -> ExperienceItem | None:
        with self._lock:
            for s in self._all_scopes():
                items = self._load_scope(s)
                for i, item in enumerate(items):
                    if item.id != item_id:
                        continue
                    patch = dict(data)
                    patch.pop("id", None)
                    # 若 scope 变化，先记录原 scope 以便迁移
                    new_scope = patch.pop("scope", None)
                    for k, v in patch.items():
                        setattr(item, k, v)
                    item.updated_at = now_iso()
                    if new_scope and new_scope != s:
                        items.pop(i)
                        self._save_scope(s)
                        item.scope = new_scope
                        target = self._load_scope(new_scope)
                        target.append(item)
                        self._save_scope(new_scope)
                    else:
                        self._save_scope(s)
                    return item
        return None

    def delete(self, item_id: str) -> bool:
        with self._lock:
            for s in self._all_scopes():
                items = self._load_scope(s)
                before = len(items)
                items = [i for i in items if i.id != item_id]
                if len(items) != before:
                    self._cache[s] = items
                    self._save_scope(s)
                    return True
        return False

    def increment_hit(self, item_id: str) -> None:
        with self._lock:
            for s in self._all_scopes():
                for item in self._load_scope(s):
                    if item.id == item_id:
                        item.hit_count += 1
                        self._save_scope(s)
                        return

    def set_embedding(self, item_id: str, vector: list[float], model: str) -> None:
        with self._lock:
            for s in self._all_scopes():
                for item in self._load_scope(s):
                    if item.id == item_id:
                        item.embedding = vector
                        item.embedding_model = model
                        self._save_scope(s)
                        return

    # ------------------------------------------------------------------
    # 向量索引维护
    # ------------------------------------------------------------------
    def items_missing_embedding(self, model: str) -> list[ExperienceItem]:
        return [i for s in self._all_scopes() for i in self._load_scope(s)
                if i.enabled and (i.embedding is None or i.embedding_model != model)]
