"""
File Watcher Receiver — 基于 watchdog 的文件系统监视器
监视指定目录的文件创建/修改事件，读取文件内容入队列
"""

import asyncio
import logging
import os
import time
import threading
from pathlib import Path
from typing import Callable

from .store import ReceiverConfig
from .manager import ReceivedData

logger = logging.getLogger("baize.receivers.file_watcher")

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    logger.warning("watchdog not installed, file watcher disabled. pip install watchdog")


class FileWatcherReceiver:
    """
    文件监视接收器
    - 监视目录下匹配模式的文件创建/修改
    - 异步读取文件内容，推入队列
    - 支持多种格式：PDF、HTML、JSON、纯文本等
    """

    def __init__(self, cfg: ReceiverConfig):
        self._cfg = cfg
        self._observer: "Observer | None" = None
        if not HAS_WATCHDOG:
            raise ImportError("watchdog is required for file watcher. Install: pip install watchdog")

    @property
    def receiver_id(self) -> str:
        return self._cfg.id

    async def run(self, on_data: Callable[[str, ReceivedData], None]):
        """在独立线程中启动 watchdog Observer"""
        watch_dir = self._cfg.watch_dir
        if not watch_dir or not os.path.isdir(watch_dir):
            logger.error(f"FileWatcherReceiver [{self._cfg.id}] invalid watch_dir: {watch_dir}")
            return

        # 保存事件循环引用：watchdog 在独立线程中回调，该线程内没有运行中的
        # 事件循环，调用 asyncio.get_running_loop() 会抛 RuntimeError。
        loop = asyncio.get_running_loop()

        patterns = [p.strip() for p in self._cfg.watch_patterns.split(",") if p.strip()] or ["*"]
        logger.info(
            f"FileWatcherReceiver [{self._cfg.id}] watching {watch_dir} "
            f"patterns={patterns} recursive={self._cfg.watch_recursive}"
        )

        class Handler(FileSystemEventHandler):
            def __init__(self, receiver_id: str, loop: asyncio.AbstractEventLoop):
                self._on_data = on_data
                self._receiver_id = receiver_id
                self._loop = loop
                self._processed: set[str] = set()

            def on_created(self, event):
                if not event.is_directory:
                    self._handle(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    self._handle(event.src_path)

            def _handle(self, path: str):
                # 防重复（文件可能已被删除，getsize 失败则跳过）
                try:
                    size = os.path.getsize(path)
                except OSError:
                    return
                key = f"{path}:{size}"
                if key in self._processed:
                    return
                self._processed.add(key)
                # 限长
                if len(self._processed) > 10000:
                    self._processed.clear()

                fname = os.path.basename(path)
                ext = os.path.splitext(fname)[1].lower()
                content_type_map = {
                    ".pdf": "pdf",
                    ".html": "html",
                    ".htm": "html",
                    ".json": "json",
                    ".xml": "xml",
                    ".csv": "csv",
                    ".log": "text",
                    ".txt": "text",
                }
                content_type = content_type_map.get(ext, "binary")

                try:
                    if content_type in ("pdf", "binary"):
                        # 二进制文件原样读取
                        with open(path, "rb") as f:
                            raw = f.read()
                    else:
                        # 文本文件
                        with open(path, "rb") as f:
                            raw = f.read()
                except Exception as e:
                    logger.error(f"FileWatcherReceiver read error: {path} - {e}")
                    return

                rd = ReceivedData(
                    receiver_id=self._receiver_id,
                    content_type=content_type,
                    raw_payload=raw,
                    source=fname,
                    metadata={
                        "file_path": path,
                        "file_name": fname,
                        "extension": ext,
                        "size": len(raw),
                    },
                )
                # 在事件循环中回调（watchdog 线程 -> 事件循环线程）
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._on_data(self._receiver_id, rd), self._loop
                    )
                except RuntimeError:
                    # 事件循环已关闭（shutdown 竞态），忽略
                    pass

        self._observer = Observer()
        self._observer.schedule(
            Handler(self._cfg.id, loop),
            watch_dir,
            recursive=self._cfg.watch_recursive,
        )
        self._observer.start()
        logger.info(f"FileWatcherReceiver [{self._cfg.id}] started")

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=3)
