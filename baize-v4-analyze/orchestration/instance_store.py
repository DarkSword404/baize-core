"""
流水线实例存储（正式两级模型："流水线模板" → "流水线实例"）。

- 模板（template）：图定义（内置只读模板 / 用户自定义模板），可编辑、查看、删除；
- 实例（instance）：由某个模板创建的具体流水线，承载运行生命周期：
    创建 / 删除 / 启用（绑定接收器，开始并行消费）/ 停用 / 历史。

实例在创建时对模板做一次**快照**（template_snapshot），因此模板后续编辑
不会影响已创建的实例；需要同步最新模板可用 `sync_from_template()`。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENCY = 10


@dataclass
class PipelineInstance:
    id: str
    name: str
    template_id: str
    type: str = "auto"                 # auto（接收器触发）/ manual（人工触发）
    description: str = ""
    receiver_id: str = ""              # 绑定的接收器
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    enabled: bool = False              # 启用 = 激活运行
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_error: str = ""
    template_snapshot: dict[str, Any] = field(default_factory=dict)  # 创建时的模板快照

    def to_dict(self, include_template: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "template_id": self.template_id,
            "type": self.type,
            "receiver_id": self.receiver_id,
            "max_concurrency": self.max_concurrency,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
        }
        if include_template:
            data["template_snapshot"] = self.template_snapshot
        return data

    def to_brief(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "template_id": self.template_id,
            "type": self.type,
            "receiver_id": self.receiver_id,
            "max_concurrency": self.max_concurrency,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def default_instances_dir() -> Path:
    base = os.environ.get("BAIZE_DATA_DIR", "~/.baize")
    return Path(base).expanduser() / "custom" / "instances"


class PipelineInstanceStore:
    """实例以独立 JSON 文件存储于 <data>/custom/instances/<id>.json。"""

    def __init__(self, base_dir: str | Path | None = None):
        self._dir = Path(base_dir) if base_dir else default_instances_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 路径辅助
    def _path(self, instance_id: str) -> Path:
        return self._dir / f"{instance_id}.json"

    # ------------------------------------------------------------ CRUD
    def create(
        self,
        name: str,
        template: dict[str, Any],
        *,
        template_id: str = "",
        description: str = "",
        receiver_id: str = "",
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> PipelineInstance:
        """由模板创建实例（对模板做快照）。"""
        with self._lock:
            instance_id = uuid.uuid4().hex[:12]
            snapshot = copy.deepcopy(template or {})
            # 快照里记录实例侧约束，保证运行时可独立解析
            snapshot.setdefault("id", template_id or snapshot.get("id", ""))
            instance = PipelineInstance(
                id=instance_id,
                name=name or snapshot.get("name", "未命名流水线"),
                template_id=template_id or snapshot.get("id", ""),
                type=str(snapshot.get("type") or "auto"),
                description=description or snapshot.get("description", ""),
                receiver_id=receiver_id,
                max_concurrency=_clamp_concurrency(max_concurrency),
                created_at=time.time(),
                updated_at=time.time(),
                template_snapshot=snapshot,
            )
            self._dir.mkdir(parents=True, exist_ok=True)
            self._dir.joinpath(f"{instance_id}.json").write_text(
                json.dumps(instance.to_dict(include_template=True), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"创建流水线实例 {instance.id} ({instance.name}, 模板 {instance.template_id})")
            return instance

    def get(self, instance_id: str) -> PipelineInstance | None:
        with self._lock:
            return self._load(instance_id)

    def list(self) -> list[PipelineInstance]:
        with self._lock:
            out = []
            for p in sorted(self._dir.glob("*.json")):
                inst = self._load(p.stem)
                if inst:
                    out.append(inst)
            return out

    def update(
        self,
        instance_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        receiver_id: str | None = None,
        max_concurrency: int | None = None,
    ) -> PipelineInstance | None:
        with self._lock:
            inst = self._load(instance_id)
            if not inst:
                return None
            if name is not None:
                inst.name = name.strip() or inst.name
            if description is not None:
                inst.description = description
            if receiver_id is not None:
                inst.receiver_id = receiver_id
            if max_concurrency is not None:
                inst.max_concurrency = _clamp_concurrency(max_concurrency)
            inst.updated_at = time.time()
            self._save(inst)
            return inst

    def delete(self, instance_id: str) -> bool:
        with self._lock:
            p = self._path(instance_id)
            if p.exists():
                p.unlink()
                return True
            return False

    # ------------------------------------------------------------ 运行相关
    def set_enabled(self, instance_id: str, enabled: bool) -> PipelineInstance | None:
        with self._lock:
            inst = self._load(instance_id)
            if not inst:
                return None
            inst.enabled = enabled
            if enabled:
                inst.last_error = ""
            inst.updated_at = time.time()
            self._save(inst)
            return inst

    def sync_from_template(self, instance_id: str, template: dict[str, Any]) -> PipelineInstance | None:
        """把实例快照更新到模板最新定义（保持实例 id/接收器/并发上限不变）。"""
        with self._lock:
            inst = self._load(instance_id)
            if not inst:
                return None
            snapshot = copy.deepcopy(template or {})
            snapshot.setdefault("id", inst.template_id)
            # 名称、描述、类型跟随模板版本同步
            inst.template_snapshot = snapshot
            if template.get("name"):
                inst.name = template["name"]
            if template.get("description") is not None:
                inst.description = template.get("description", "")
            if template.get("type"):
                inst.type = str(template["type"])
            inst.updated_at = time.time()
            self._save(inst)
            return inst

    # ------------------------------------------------------------ 内部
    def _load(self, instance_id: str) -> PipelineInstance | None:
        p = self._path(instance_id)
        if not p.exists():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"实例文件损坏 {p}: {e}")
            return None
        try:
            inst = PipelineInstance(
                id=raw.get("id") or instance_id,
                name=raw.get("name") or "未命名流水线",
                template_id=raw.get("template_id") or "",
                type=str(raw.get("type") or "auto"),
                description=raw.get("description", ""),
                receiver_id=raw.get("receiver_id", ""),
                max_concurrency=_clamp_concurrency(raw.get("max_concurrency") or DEFAULT_MAX_CONCURRENCY),
                enabled=bool(raw.get("enabled", False)),
                created_at=raw.get("created_at") or 0.0,
                updated_at=raw.get("updated_at") or 0.0,
                last_error=raw.get("last_error", ""),
                template_snapshot=raw.get("template_snapshot") or {},
            )
            return inst
        except Exception as e:  # noqa: BLE001
            logger.warning(f"解析实例 {p} 失败: {e}")
            return None

    def _save(self, inst: PipelineInstance) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._dir.joinpath(f"{inst.id}.json").write_text(
            json.dumps(inst.to_dict(include_template=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _clamp_concurrency(value: int) -> int:
    try:
        return max(1, min(int(value), 128))
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENCY


_instances: PipelineInstanceStore | None = None
_instances_lock = threading.Lock()


def get_instance_store() -> PipelineInstanceStore:
    """进程内单例（与 runs.db 存放位置一致）。"""
    global _instances
    if _instances is None:
        with _instances_lock:
            if _instances is None:
                _instances = PipelineInstanceStore()
    return _instances
