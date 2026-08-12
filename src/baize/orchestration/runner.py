"""
后台执行管理器 — 借鉴 n8n/Temporal 的 Worker 模型。

核心职责：
1. 接收执行请求 → 创建 RunRecord → 投递到 asyncio.Task 池
2. HTTP 请求立即返回 run_id，执行在后台异步进行
3. 通过 RunStore 持久化执行状态，客户端可随时轮询或 SSE 订阅
4. 客户端断开不影响执行，重连后补齐历史事件
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Callable

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineDefinition
from baize.orchestration.compiler import PipelineGraphCompiler
from baize.orchestration.run_store import RunRecord, get_run_store

logger = logging.getLogger(__name__)

# 最大并发执行数
DEFAULT_MAX_CONCURRENT = 5


class PipelineRunner:
    """后台流水线执行引擎。

    用法::

        runner = PipelineRunner(max_concurrent=5)
        run_id = await runner.submit(pipeline_def, {"target": "1.2.3.4"})
        # 立即返回，执行在后台进行
    """

    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._store = get_run_store()
        self._events: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        # 每个 run_id → 订阅者队列列表

    # ------------------------------------------------------------------
    # Public — 启动执行
    # ------------------------------------------------------------------

    async def submit(
        self,
        pipeline: PipelineDefinition,
        context: dict[str, Any],
        webhook: str = "",
    ) -> str:
        """提交一次执行，返回 run_id。

        执行在后台异步进行，调用者不阻塞。"""
        run_id = str(uuid.uuid4())

        record = RunRecord(
            run_id=run_id,
            pipeline_id=pipeline.id,
            pipe_type=pipeline.type,
            status="pending",
        )
        record.context = context
        record.webhook = webhook
        self._store.create(record)

        # 投入后台执行
        asyncio.create_task(self._execute(pipeline, run_id, context, webhook))

        return run_id

    # ------------------------------------------------------------------
    # Public — 查询状态
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._store.get(run_id)

    def list_runs(
        self,
        pipeline_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self._store.list_runs(pipeline_id=pipeline_id, status=status, limit=limit)

    # ------------------------------------------------------------------
    # Public — SSE 事件流（支持重连补齐）
    # ------------------------------------------------------------------

    async def subscribe_events(
        self,
        run_id: str,
        last_event_id: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """SSE 生成器：先补齐历史事件，再推送实时事件。

        客户端重连时传入 last_event_id，只推送增量。
        """
        # 1. 补齐历史事件
        history = self._store.get_events_since(run_id, last_event_id)
        for event in history:
            yield event

        # 2. 检查是否已完成
        record = self._store.get(run_id)
        if record and record.status in ("completed", "failed"):
            yield {
                "event_id": str(uuid.uuid4()),
                "type": f"pipeline_{record.status}",
                "run_id": run_id,
                "timestamp": time.time(),
                "data": {"report": record.report, "error": record.error},
            }
            yield {"event_id": "done", "type": "done", "run_id": run_id, "timestamp": time.time(), "data": {}}
            return

        # 3. 订阅实时事件
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if run_id not in self._events:
            self._events[run_id] = []
        self._events[run_id].append(queue)

        try:
            while True:
                event = await queue.get()
                yield event
                if event.get("type") in ("pipeline_completed", "pipeline_failed", "done"):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            # 清理订阅者
            if run_id in self._events:
                queues = self._events[run_id]
                if queue in queues:
                    queues.remove(queue)
                if not queues:
                    del self._events[run_id]

    async def resume_after_confirm(self, run_id: str, choice: str) -> RunRecord | None:
        """恢复被人工确认中断的流水线。"""
        record = self._store.get(run_id)
        if not record or record.status != "paused":
            return None

        # 获取 pipeline 定义缓存
        pipeline_def = self._get_pipeline_def(record.pipeline_id)
        if pipeline_def is None:
            self._store.update_status(run_id, "failed", "管道定义未找到")
            return self._store.get(run_id)

        # 恢复执行（在后台任务中）
        self._store.update_status(run_id, "running")
        asyncio.create_task(
            self._execute_resume(pipeline_def, run_id, choice)
        )
        return record

    # ------------------------------------------------------------------
    # Internal — 后台执行
    # ------------------------------------------------------------------

    async def _execute(
        self,
        pipeline: PipelineDefinition,
        run_id: str,
        context: dict[str, Any],
        webhook: str,
    ) -> None:
        """后台执行主循环。"""
        async with self._semaphore:
            self._store.update_status(run_id, "running")

            compiler = PipelineGraphCompiler(pipeline)
            cfg = {"configurable": {"thread_id": run_id}}

            try:
                await compiler.execute_stream(
                    context=context,
                    webhook=webhook,
                    config=cfg,
                    on_event=lambda etype, edata: self._handle_event(run_id, etype, edata),
                )

                # 读取最终状态
                compiled = compiler.compile()
                final = compiled.get_state(cfg)
                if final and final.values:
                    self._store.set_final_state(run_id, final.values)
                else:
                    self._store.update_status(run_id, "completed")

                self._push_event(run_id, {
                    "event_id": str(uuid.uuid4()),
                    "type": "pipeline_completed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {},
                })
                self._push_event(run_id, {
                    "event_id": "done",
                    "type": "done",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {},
                })

                # webhook 回调（如果有）
                if webhook:
                    try:
                        await self._send_webhook(webhook, run_id, "completed")
                    except Exception as e:
                        logger.warning(f"Webhook 回调失败 {webhook}: {e}")

            except Exception as e:
                logger.exception(f"流水线执行失败 {pipeline.id}/{run_id}")
                self._store.update_status(run_id, "failed", str(e))
                self._push_event(run_id, {
                    "event_id": str(uuid.uuid4()),
                    "type": "pipeline_failed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {"error": str(e)},
                })
                self._push_event(run_id, {
                    "event_id": "done",
                    "type": "done",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {},
                })

                if webhook:
                    try:
                        await self._send_webhook(webhook, run_id, "failed", str(e))
                    except Exception:
                        pass

    async def _execute_resume(
        self,
        pipeline: PipelineDefinition,
        run_id: str,
        choice: str,
    ) -> None:
        """恢复执行（人工确认后）。"""
        async with self._semaphore:
            try:
                compiler = PipelineGraphCompiler(pipeline)
                result = await compiler.resume_after_confirm(run_id, choice)
                self._store.set_final_state(run_id, result)
                self._push_event(run_id, {
                    "event_id": str(uuid.uuid4()),
                    "type": "pipeline_completed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {},
                })
                self._push_event(run_id, {
                    "event_id": "done",
                    "type": "done",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {},
                })
            except Exception as e:
                logger.exception(f"恢复执行失败 {run_id}")
                self._store.update_status(run_id, "failed", str(e))
                self._push_event(run_id, {
                    "event_id": str(uuid.uuid4()),
                    "type": "pipeline_failed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {"error": str(e)},
                })

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    def _handle_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        """处理编译器回调事件。"""
        event_id = str(uuid.uuid4())
        event = {
            "event_id": event_id,
            "type": event_type,
            "run_id": run_id,
            "timestamp": time.time(),
            "data": data,
        }
        self._store.add_event(run_id, event)

        # 处理暂停状态
        if event_type == "pipeline_paused":
            self._store.update_status(run_id, "paused")

        # 处理节点记录
        node_id = data.get("node_id", "")
        if node_id and event_type in ("node_started", "node_completed", "node_failed"):
            node_record = data.get("data", {})
            if isinstance(node_record, dict):
                self._store.add_node_record(run_id, node_id, {
                    **node_record,
                    "status": event_type.replace("node_", ""),
                })

        # 推送实时事件
        self._push_event(run_id, event)

    def _push_event(self, run_id: str, event: dict[str, Any]) -> None:
        """向所有订阅者推送事件。"""
        queues = self._events.get(run_id, [])
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------
    # Pipeline 定义缓存
    # ------------------------------------------------------------------

    _pipeline_cache: dict[str, PipelineDefinition] = {}

    def cache_pipeline(self, pipeline: PipelineDefinition) -> None:
        self._pipeline_cache[pipeline.id] = pipeline

    def _get_pipeline_def(self, pipeline_id: str) -> PipelineDefinition | None:
        return self._pipeline_cache.get(pipeline_id)

    # Webhook 回调函数（默认使用 httpx）
    async def _send_webhook(
        self,
        url: str,
        run_id: str,
        status: str,
        error: str = "",
    ) -> None:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, json={
                    "run_id": run_id,
                    "status": status,
                    "error": error,
                })
        except Exception as e:
            logger.warning(f"Webhook 发送失败: {e}")


# ====================================================================
# 全局实例
# ====================================================================

_default_runner = PipelineRunner()


def get_runner() -> PipelineRunner:
    return _default_runner
