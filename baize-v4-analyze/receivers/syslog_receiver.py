"""
Syslog Receiver — 基于 asyncio UDP 的高并发 Syslog 服务器
兼容 RFC 3164 / RFC 5424
使用标准库 asyncio + socket，无需额外依赖，轻松应对数千条/秒
"""

import asyncio
import logging
import socket
import time
from typing import Callable

from .store import ReceiverConfig
from .manager import ReceivedData

logger = logging.getLogger("baize.receivers.syslog")

# 最大 UDP 数据报大小（通常 65535，Syslog 建议 8192）
MAX_DGRAM = 65535


class SyslogReceiver:
    """UDP Syslog 接收器 — 高性能异步实现"""

    def __init__(self, cfg: ReceiverConfig):
        self._cfg = cfg
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: "_SyslogProtocol" | None = None

    @property
    def receiver_id(self) -> str:
        return self._cfg.id

    async def run(self, on_data: Callable[[str, ReceivedData], None]):
        """启动 UDP 监听"""
        loop = asyncio.get_running_loop()
        try:
            self._transport, self._protocol = await loop.create_datagram_endpoint(
                lambda: _SyslogProtocol(self._cfg, on_data),
                local_addr=(self._cfg.syslog_host, self._cfg.syslog_port),
            )
            logger.info(
                f"SyslogReceiver [{self._cfg.id}] listening on {self._cfg.syslog_host}:{self._cfg.syslog_port}"
            )
        except OSError as e:
            logger.error(f"SyslogReceiver [{self._cfg.id}] failed to bind: {e}")
            raise


class _SyslogProtocol(asyncio.DatagramProtocol):
    """UDP 数据报协议处理"""

    def __init__(self, cfg: ReceiverConfig, on_data: Callable[[str, ReceivedData], None]):
        self._cfg = cfg
        self._on_data = on_data

    def connection_made(self, transport: asyncio.DatagramTransport):
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple):
        """每收到一个 UDP 数据报（一条 Syslog 消息），立即创建协程处理"""
        asyncio.create_task(self._handle(data, addr))

    async def _handle(self, data: bytes, addr: tuple):
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = data.hex()

        rd = ReceivedData(
            receiver_id=self._cfg.id,
            content_type="syslog",
            raw_payload=data,
            source=f"{addr[0]}:{addr[1]}",
            metadata={"addr": addr[0], "port": addr[1], "size": len(data)},
        )
        await self._on_data(self._cfg.id, rd)
