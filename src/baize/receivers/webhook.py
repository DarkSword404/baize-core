"""
Webhook Receiver — 基于 FastAPI 的 HTTP 数据接收端点
"""

import logging
import time
from typing import Optional
from fastapi import Request, Response
from .manager import ReceiverManager
from .store import ReceiverStore

logger = logging.getLogger("baize.receivers.webhook")


async def handle_webhook(request: Request, path: str) -> Response:
    """
    通用 Webhook 处理器
    路由 /api/v1/hook/{path} → 查找匹配 path 的 webhook 接收器
    """
    store = ReceiverStore.get_instance()
    manager = ReceiverManager.get()

    # 查找匹配此 webhook_path 的接收器
    receiver_id: Optional[str] = None
    for cfg in store.list_all():
        if cfg.kind == "webhook" and cfg.enabled and cfg.webhook_path == path:
            receiver_id = cfg.id
            break

    if receiver_id is None:
        # 也尝试直接用 path 作为 receiver_id
        cfg = store.get(path)
        if cfg and cfg.kind == "webhook" and cfg.enabled:
            receiver_id = path

    if receiver_id is None:
        return Response(content="receiver not found", status_code=404)

    # 读取原始数据
    content_type = request.headers.get("content-type", "application/octet-stream")
    raw = await request.body()

    # 元数据
    source = request.client.host if request.client else "unknown"
    metadata = {
        "method": request.method,
        "headers": dict(request.headers),
        "query_params": dict(request.query_params),
        "path": path,
    }

    accepted = manager.accept_webhook(
        receiver_id=receiver_id,
        data=raw,
        content_type=content_type,
        source=source,
        metadata=metadata,
    )

    if accepted:
        return Response(
            content='{"status":"accepted","receiver_id":"' + receiver_id + '"}',
            status_code=202,
            media_type="application/json",
        )
    else:
        return Response(
            content='{"status":"rejected","reason":"receiver not enabled or manager not running"}',
            status_code=503,
            media_type="application/json",
        )
