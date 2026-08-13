"""
Webhook Receiver — 基于 FastAPI 的 HTTP 数据接收端点
"""

import hmac
import logging
import os
from typing import Optional
from fastapi import HTTPException, Request, Response
from .manager import ReceiverManager
from .store import ReceiverStore

logger = logging.getLogger("baize.receivers.webhook")

# 允许进入 metadata 的非敏感请求头（避免 Authorization/Cookie 等泄露）
_SAFE_HEADERS = {
    "content-type",
    "content-length",
    "user-agent",
    "host",
    "x-forwarded-for",
    "x-real-ip",
    "x-baize-webhook-key",
}


def _validate_webhook_key(request: Request) -> None:
    """若配置了 BAIZE_WEBHOOK_API_KEY，则要求请求携带匹配密钥。

    未配置时保持匿名访问，向后兼容外部系统（SIEM 等）推送。
    """
    expected = os.getenv("BAIZE_WEBHOOK_API_KEY", "")
    if not expected:
        return
    supplied = request.headers.get("X-Baize-Webhook-Key") or request.query_params.get(
        "key", ""
    )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="无效的 Webhook 密钥")


async def handle_webhook(request: Request, path: str) -> Response:
    """
    通用 Webhook 处理器
    路由 /api/v1/hook/{path} → 查找匹配 path 的 webhook 接收器
    """
    _validate_webhook_key(request)
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

    # 元数据（仅保留白名单请求头，避免敏感头进入下游）
    source = request.client.host if request.client else "unknown"
    metadata = {
        "method": request.method,
        "headers": {
            k: v for k, v in request.headers.items() if k.lower() in _SAFE_HEADERS
        },
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
