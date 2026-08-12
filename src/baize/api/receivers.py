"""
Receivers API — 接收器 CRUD + 数据查询
"""

import asyncio
import logging
import uuid
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from typing import Optional

from ..receivers.store import ReceiverStore, ReceiverConfig
from ..receivers.manager import ReceiverManager

logger = logging.getLogger("baize.api.receivers")

router = APIRouter()


# ---- 请求/响应模型 ----

class ReceiverCreateRequest(BaseModel):
    name: str
    kind: str = "webhook"  # webhook / syslog / file
    pipeline_id: str = ""
    webhook_path: str = ""
    syslog_port: int = 0
    syslog_host: str = "0.0.0.0"
    syslog_protocol: str = "udp"
    watch_dir: str = ""
    watch_patterns: str = "*"
    watch_recursive: bool = False


class ReceiverUpdateRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    pipeline_id: Optional[str] = None
    webhook_path: Optional[str] = None
    syslog_port: Optional[int] = None
    syslog_host: Optional[str] = None
    syslog_protocol: Optional[str] = None
    watch_dir: Optional[str] = None
    watch_patterns: Optional[str] = None
    watch_recursive: Optional[bool] = None


# ---- 路由 ----

@router.get("/receivers")
async def list_receivers():
    """列出所有接收器"""
    store = ReceiverStore.get_instance()
    manager = ReceiverManager.get()
    receivers = store.list_all()
    result = []
    for r in receivers:
        d = r.model_dump()
        d["queue_size"] = await manager.peek(r.id)
        result.append(d)
    return {"receivers": result}


@router.post("/receivers")
async def create_receiver(req: ReceiverCreateRequest):
    """创建新接收器"""
    store = ReceiverStore.get_instance()
    cfg = ReceiverConfig(
        name=req.name,
        kind=req.kind,
        enabled=False,  # 创建后默认禁用，需手动启用
        pipeline_id=req.pipeline_id,
        webhook_path=req.webhook_path,
        syslog_port=req.syslog_port,
        syslog_host=req.syslog_host,
        syslog_protocol=req.syslog_protocol,
        watch_dir=req.watch_dir,
        watch_patterns=req.watch_patterns,
        watch_recursive=req.watch_recursive,
    )
    cfg = store.create(cfg)
    logger.info(f"Receiver created: {cfg.id} ({cfg.name})")
    return {"receiver": cfg.model_dump()}


@router.get("/receivers/{receiver_id}")
async def get_receiver(receiver_id: str):
    """获取单个接收器"""
    store = ReceiverStore.get_instance()
    cfg = store.get(receiver_id)
    if cfg is None:
        raise HTTPException(404, "receiver not found")
    manager = ReceiverManager.get()
    d = cfg.model_dump()
    d["queue_size"] = await manager.peek(receiver_id)
    return {"receiver": d}


@router.put("/receivers/{receiver_id}")
async def update_receiver(receiver_id: str, req: ReceiverUpdateRequest):
    """更新接收器"""
    store = ReceiverStore.get_instance()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    cfg = store.update(receiver_id, **updates)
    if cfg is None:
        raise HTTPException(404, "receiver not found")

    manager = ReceiverManager.get()
    if "enabled" in updates:
        if updates["enabled"]:
            await manager.enable_receiver(receiver_id)
        else:
            await manager.disable_receiver(receiver_id)

    return {"receiver": cfg.model_dump()}


@router.delete("/receivers/{receiver_id}")
async def delete_receiver(receiver_id: str):
    """删除接收器"""
    store = ReceiverStore.get_instance()
    manager = ReceiverManager.get()
    await manager.disable_receiver(receiver_id)
    ok = store.delete(receiver_id)
    if not ok:
        raise HTTPException(404, "receiver not found")
    return {"status": "deleted"}


@router.get("/receivers/{receiver_id}/data")
async def get_receiver_data(receiver_id: str, max_wait: float = 2.0):
    """从接收器队列拉取一条数据（供流水线节点消费）"""
    manager = ReceiverManager.get()
    data = await manager.consume(receiver_id, max_wait=min(max_wait, 30.0))
    if data is None:
        return {"status": "empty", "data": None}
    return {
        "status": "ok",
        "data": {
            "receiver_id": data.receiver_id,
            "timestamp": data.timestamp,
            "source": data.source,
            "content_type": data.content_type,
            "payload": data.raw_payload.hex() if data.content_type == "binary" else data.raw_payload.decode("utf-8", errors="replace"),
            "payload_size": len(data.raw_payload),
            "metadata": data.metadata,
        },
    }
