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
import os
import time
import uuid
from typing import Any, AsyncGenerator, Callable

from baize.orchestration.state import PipelineState
from baize.orchestration.node_types import PipelineDefinition
from baize.orchestration.compiler import PipelineGraphCompiler
from baize.orchestration.run_store import RunRecord, get_run_store

logger = logging.getLogger(__name__)

# 进程级最大并发执行数（全局兜底背压，防止多个实例/手动 run 叠加压垮模型提供商）。
# 每个实例自己的并行度由实例的 max_concurrency 控制（默认 10，见 instance_store.DEFAULT_MAX_CONCURRENCY），
# 故全局兜底默认需高于单实例上限，避免成为瓶颈；可用环境变量 BAIZE_PIPELINE_MAX_CONCURRENT 覆盖。
DEFAULT_MAX_CONCURRENT = max(1, int(os.environ.get("BAIZE_PIPELINE_MAX_CONCURRENT", "20")))


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

    def _recycle_dialog_if_discarded(self, run_id: str) -> None:
        """对话生命周期终态钩子：run 正常完成且 end 节点标记 discard 时回收对话。"""
        try:
            done = self._store.get(run_id)
            if done and done.status == "completed" and done.dialog_action == "discard":
                self._store.discard_dialog(run_id)
        except Exception:  # noqa: BLE001 — 回收失败不应阻塞 run 终态事件
            logger.exception(f"run {run_id} 对话回收失败（忽略）")

    async def submit(
        self,
        pipeline: PipelineDefinition,
        context: dict[str, Any],
        webhook: str = "",
        dedup_key: str = "",
    ) -> str:
        """提交一次执行，返回 run_id。

        执行在后台异步进行，调用者不阻塞。

        dedup_key 用于幂等去重：同一事件（如 webhook 重放）重复提交时，
        直接返回既有 run_id，不创建新 run。"""
        run_id = str(uuid.uuid4())

        record = RunRecord(
            run_id=run_id,
            pipeline_id=pipeline.id,
            pipe_type=pipeline.type,
            status="pending",
        )
        record.context = context
        record.webhook = webhook
        record.dedup_key = dedup_key
        created = self._store.create(record)

        # 已存在相同 dedup_key 的记录 → 幂等返回既有 run，不重复执行
        if created.run_id != run_id:
            return created.run_id

        # 投入后台执行
        asyncio.create_task(self._execute(pipeline, run_id, context, webhook))

        return run_id

    async def execute_one(
        self,
        pipeline: PipelineDefinition,
        context: dict[str, Any],
        webhook: str = "",
        dedup_key: str = "",
    ) -> RunRecord | None:
        """同步执行一次并等待终态（长驻会话逐条处理告警用）。

        内部复用 submit() 的执行路径与 dedup 幂等语义：
        - dedup_key 命中既有记录时直接返回既有 run（不重复执行）；
        - 否则提交后台任务并轮询 store 直到 completed / failed / paused。
        """
        run_id = await self.submit(pipeline, context, webhook, dedup_key)
        record = self._store.get(run_id)
        if record is None:
            return None
        if record.status in ("completed", "failed", "paused"):
            return record

        timeout = float(getattr(pipeline, "timeout_seconds", None) or 3600) + 60.0
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            record = self._store.get(run_id)
            if record and record.status in ("completed", "failed", "paused"):
                return record
        logger.warning(f"execute_one 等待 {pipeline.id}/{run_id} 超时（{timeout}s）")
        return self._store.get(run_id)

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
    # Public — 服务重启恢复
    # ------------------------------------------------------------------

    async def recover_interrupted(self) -> list[str]:
        """服务重启后恢复中断的 run。

        扫描 status in (pending, running) 的记录并重新投递执行；
        找不到管道定义的标记为 failed。"""
        recovered: list[str] = []
        records = self._store.list_by_status(["pending", "running"])
        for rec in records:
            pipeline_def = self._get_pipeline_def(rec.pipeline_id)
            if pipeline_def is None:
                self._store.update_status(
                    rec.run_id, "failed", "服务重启后管道定义未找到，无法恢复"
                )
                continue
            self._store.update_status(rec.run_id, "running")
            asyncio.create_task(
                self._execute(pipeline_def, rec.run_id, rec.context, rec.webhook)
            )
            recovered.append(rec.run_id)
        if recovered:
            logger.info(f"恢复 {len(recovered)} 个中断的 run: {recovered}")
        return recovered

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
                # execute_stream 是 async generator：必须 async for 消费（P0-1）。
                # 节点事件 / pipeline_completed / pipeline_failed / pipeline_paused
                # 均由 on_event 回调推送并入库。
                final: dict[str, Any] = {}
                async for _run_id, final_values in compiler.execute_stream(
                    context=context,
                    webhook=webhook,
                    config=cfg,
                    on_event=lambda etype, edata: self._handle_event(run_id, etype, edata),
                ):
                    if final_values:
                        final = final_values

                # 人工确认暂停：execute_stream 已推送 pipeline_paused 并更新状态，等待 resume
                record = self._store.get(run_id)
                if record and record.status == "paused":
                    return

                # 初始 status="pending"，正常跑完未被改写 → 视为 completed
                status = (final or {}).get("status", "pending")
                if status == "pending":
                    final = {**(final or {}), "status": "completed"}
                    status = "completed"

                if status == "failed":
                    self._store.set_final_state(run_id, final)
                    self._push_event(run_id, {
                        "event_id": str(uuid.uuid4()),
                        "type": "pipeline_failed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "data": {"error": final.get("error", ""), "report": final.get("report", "")},
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
                            await self._send_webhook(webhook, run_id, "failed", final.get("error", ""))
                        except Exception:
                            pass
                    return

                self._store.set_final_state(run_id, final)
                self._recycle_dialog_if_discarded(run_id)
                self._push_event(run_id, {
                    "event_id": str(uuid.uuid4()),
                    "type": "pipeline_completed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {"report": final.get("report", "")},
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
                result = dict(result or {})
                # 初始 status="pending"，resume 跑完未被改写 → 视为 completed
                if result.get("status", "pending") == "pending":
                    result["status"] = "completed"
                self._store.set_final_state(run_id, result)
                self._recycle_dialog_if_discarded(run_id)
                done_record = self._store.get(run_id)
                done_report = done_record.report if done_record else result.get("report", "")
                self._push_event(run_id, {
                    "event_id": str(uuid.uuid4()),
                    "type": "pipeline_completed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {"report": done_report},
                })
                self._push_event(run_id, {
                    "event_id": "done",
                    "type": "done",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {},
                })
                webhook = self._store.get(run_id)
                if webhook and webhook.webhook:
                    try:
                        await self._send_webhook(webhook.webhook, run_id, "completed")
                    except Exception:
                        pass
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
