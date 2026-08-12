"""
Receiver Store — 持久化存储接收器配置
文件位置: ~/.baize/receivers.json
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


class ReceiverConfig(BaseModel):
    """单个接收器配置"""
    id: str = Field(default="", description="唯一标识")
    name: str = Field(default="", description="接收器名称")
    kind: str = Field(default="webhook", description="类型: webhook / syslog / file")
    enabled: bool = Field(default=False, description="是否启用")
    pipeline_id: str = Field(default="", description="绑定的流水线 ID")
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    # Webhook config
    webhook_path: str = Field(default="", description="Webhook 路径，如 /hook/my-receiver")

    # Syslog config
    syslog_port: int = Field(default=0, description="Syslog UDP 端口")
    syslog_host: str = Field(default="0.0.0.0", description="Syslog 绑定地址")
    syslog_protocol: str = Field(default="udp", description="udp 或 tcp")

    # File watcher config
    watch_dir: str = Field(default="", description="要监视的目录")
    watch_patterns: str = Field(default="*", description="文件匹配模式，逗号分隔，如 *.pdf,*.html,*.json")
    watch_recursive: bool = Field(default=False, description="是否递归监视")

    # 统计
    total_received: int = Field(default=0, description="累计接收数据条数")
    last_received_at: Optional[float] = Field(default=None, description="最后一次接收时间")


class ReceiverStore:
    """接收器持久化存储"""

    _instance: Optional["ReceiverStore"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "ReceiverStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._base = Path(os.path.expanduser("~/.baize"))
        self._base.mkdir(parents=True, exist_ok=True)
        self._path = self._base / "receivers.json"
        self._data: dict[str, ReceiverConfig] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                for item in raw.get("receivers", []):
                    cfg = ReceiverConfig(**item)
                    self._data[cfg.id] = cfg
            except Exception:
                pass

    def _save(self):
        raw = {"receivers": [r.model_dump() for r in self._data.values()]}
        self._path.write_text(json.dumps(raw, indent=2, ensure_ascii=False))

    def list_all(self) -> list[ReceiverConfig]:
        with self._lock:
            return sorted(self._data.values(), key=lambda r: r.created_at)

    def get(self, receiver_id: str) -> Optional[ReceiverConfig]:
        with self._lock:
            return self._data.get(receiver_id)

    def create(self, cfg: ReceiverConfig) -> ReceiverConfig:
        with self._lock:
            import uuid
            if not cfg.id:
                cfg.id = uuid.uuid4().hex[:12]
            cfg.created_at = time.time()
            cfg.updated_at = time.time()
            self._data[cfg.id] = cfg
            self._save()
            return cfg

    def update(self, receiver_id: str, **updates) -> Optional[ReceiverConfig]:
        with self._lock:
            cfg = self._data.get(receiver_id)
            if cfg is None:
                return None
            for key, val in updates.items():
                if hasattr(cfg, key) and key not in ("id", "created_at"):
                    setattr(cfg, key, val)
            cfg.updated_at = time.time()
            self._data[receiver_id] = cfg
            self._save()
            return cfg

    def delete(self, receiver_id: str) -> bool:
        with self._lock:
            if receiver_id in self._data:
                del self._data[receiver_id]
                self._save()
                return True
            return False

    def get_by_pipeline(self, pipeline_id: str) -> list[ReceiverConfig]:
        with self._lock:
            return [r for r in self._data.values() if r.pipeline_id == pipeline_id]
