"""
Receiver Manager — 接收器生命周期管理 & 高并发数据缓冲
"""

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .store import ReceiverStore, ReceiverConfig

logger = logging.getLogger("baize.receivers")


@dataclass
class ReceivedData:
    """单条接收数据"""
    receiver_id: str
    timestamp: float = field(default_factory=time.time)
    source: str = ""           # 来源 IP/文件名
    content_type: str = ""     # 数据类型: pdf, html, json, syslog, plain
    raw_payload: bytes = b""   # 原始数据
    metadata: dict[str, Any] = field(default_factory=dict)


class ReceiverManager:
    """
    接收器总控
    - 管理所有接收器实例的生命周期
    - 为每个接收器维护异步队列（无界），支持高并发
    - 提供数据消费接口供流水线节点拉取
    """

    _instance: Optional["ReceiverManager"] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "ReceiverManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._store = ReceiverStore.get_instance()
        self._queues: dict[str, asyncio.Queue] = {}       # receiver_id → Queue
        self._handlers: dict[str, Callable] = {}           # pipeline_id → handler
        self._listeners: list[Callable[[str, ReceivedData], None]] = []  # 全局监听
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ---- 生命周期 ----

    async def start(self):
        """启动所有已启用的接收器"""
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        for cfg in self._store.list_all():
            if cfg.enabled:
                self._queues.setdefault(cfg.id, asyncio.Queue())
                await self._start_receiver(cfg)
        logger.info(f"ReceiverManager started, {len(self._queues)} queues")

    async def stop(self):
        """停止所有接收器"""
        self._running = False
        self._handlers.clear()
        self._listeners.clear()
        # 清空队列
        for q in self._queues.values():
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._queues.clear()
        logger.info("ReceiverManager stopped")

    async def _start_receiver(self, cfg: ReceiverConfig):
        """根据类型启动具体接收器"""
        kind = cfg.kind
        if kind == "syslog":
            from .syslog_receiver import SyslogReceiver
            receiver = SyslogReceiver(cfg)
            asyncio.create_task(receiver.run(self._on_data))
        elif kind == "file":
            from .file_watcher import FileWatcherReceiver
            receiver = FileWatcherReceiver(cfg)
            asyncio.create_task(receiver.run(self._on_data))
        # webhook 不在这里启动，由 FastAPI 路由处理

    async def _on_data(self, receiver_id: str, data: ReceivedData):
        """接收器回调：数据入队列 + 更新统计"""
        queue = self._queues.get(receiver_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[receiver_id] = queue
        await queue.put(data)

        # 更新统计
        cfg = self._store.get(receiver_id)
        if cfg:
            self._store.update(receiver_id,
                               total_received=cfg.total_received + 1,
                               last_received_at=time.time())

        # 通知全局监听者
        for listener in self._listeners:
            try:
                listener(receiver_id, data)
            except Exception:
                pass

        logger.debug(f"Receiver [{receiver_id}] got data: {data.content_type}, {len(data.raw_payload)} bytes")

    # ---- Webhook 专用接口（非 async context）----

    def accept_webhook(self, receiver_id: str, data: bytes, content_type: str = "",
                       source: str = "", metadata: dict | None = None) -> bool:
        """同步入口：webhook 接收数据。由 FastAPI 端点调用。"""
        cfg = self._store.get(receiver_id)
        if cfg is None or not cfg.enabled:
            return False
        rd = ReceivedData(
            receiver_id=receiver_id,
            content_type=content_type,
            raw_payload=data,
            source=source,
            metadata=metadata or {},
        )
        loop = self._loop
        if loop is None or not loop.is_running():
            return False
        asyncio.run_coroutine_threadsafe(self._on_data(receiver_id, rd), loop)
        return True

    # ---- 数据消费 ----

    async def consume(self, receiver_id: str, max_wait: float = 5.0) -> Optional[ReceivedData]:
        """流水线节点调用：从指定接收器队列中拉取一条数据"""
        queue = self._queues.get(receiver_id)
        if queue is None:
            return None
        try:
            return await asyncio.wait_for(queue.get(), timeout=max_wait)
        except asyncio.TimeoutError:
            return None

    async def peek(self, receiver_id: str) -> int:
        """查看队列积压数量"""
        queue = self._queues.get(receiver_id)
        return 0 if queue is None else queue.qsize()

    def add_listener(self, callback: Callable[[str, ReceivedData], None]):
        """注册全局监听"""
        self._listeners.append(callback)

    # ---- 动态启停 ----

    async def enable_receiver(self, receiver_id: str):
        """动态启用接收器"""
        cfg = self._store.get(receiver_id)
        if cfg is None:
            return
        self._store.update(receiver_id, enabled=True)
        self._queues.setdefault(receiver_id, asyncio.Queue())
        await self._start_receiver(cfg)
        logger.info(f"Receiver [{receiver_id}] enabled")

    async def disable_receiver(self, receiver_id: str):
        """动态停用接收器"""
        self._store.update(receiver_id, enabled=False)
        logger.info(f"Receiver [{receiver_id}] disabled")
