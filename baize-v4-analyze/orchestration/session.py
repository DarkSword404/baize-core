"""
长驻会话管理器 — 静默告警自动研判（对应 ALERT-TRIAGE-SILENT-SESSION.md §4.3）。

每个"已激活的自动化流水线"对应一个长驻 asyncio 调度循环：
    收件箱 claim_next(receiver_id) → 独立 Worker 执行（一次图执行 = 一条 run/对话）→ complete/fail

特性：
- 持久化收件箱（~/.baize/orchestration/alerts.db）为唯一数据源；
- lease + 过期回收：进程崩溃后重启，未完成告警自动回 queued 重派；
- at-least-once + runs.dedup_key（alert:{fingerprint}）幂等兜底；
- 失败 backoff 重试，超限置 dead（死信），可经 API 重放；
- 同一流水线内为并行 Worker 池（max_concurrency 上限，默认 10）：
  每条入站数据由独立 Worker / 独立 run / 独立对话处理，互不阻塞、可并行消费；
- 走到"结束对话(end)"节点的 run 在终态回收对话（dialog 清空，历史保留摘要）。

激活态本身持久化于 runs.db 的 activations 表，服务重启后由 resume_all() 恢复会话。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from baize.orchestration.node_types import PipelineDefinition
from baize.orchestration.run_store import get_activation_store
from baize.orchestration.runner import PipelineRunner, get_runner

logger = logging.getLogger(__name__)

_IDLE_SLEEP = 0.5          # 空转轮询间隔（秒）
_RECOVER_EVERY = 30        # 周期性回收过期租约的间隔（秒）


class SessionSupervisor:
    """长驻会话调度器。"""

    def __init__(
        self,
        runner: PipelineRunner | None = None,
        activation: Any | None = None,
        inbox: Any | None = None,
    ):
        self._runner = runner or get_runner()
        self._activation = activation or get_activation_store()
        # inbox 来自 baize-core（baize-core 是编排模块的依赖）
        try:
            from baize.receivers.inbox import get_alert_inbox
            self._inbox = inbox or get_alert_inbox()
        except Exception:  # 编排模块单独运行时（无 core）降级为不可用
            self._inbox = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._stats: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public — 启停
    # ------------------------------------------------------------------

    def ensure(self, pipeline: PipelineDefinition) -> bool:
        """为已激活的自动化流水线启动长驻会话（幂等）。

        Returns: 是否成功注册会话。
        """
        if pipeline.type != "auto":
            return False
        if not self._activation.is_active(pipeline.id):
            return False
        receiver_id = self._resolve_receiver_id(pipeline)
        if not receiver_id:
            logger.warning(
                "流水线 %s 已激活但未绑定接收器（receiver 节点需配置 agent=接收器ID），"
                "跳过长驻会话", pipeline.id,
            )
            return False
        existing = self._tasks.get(pipeline.id)
        if existing and not existing.done():
            return True
        task = asyncio.create_task(
            self._loop(pipeline.id, receiver_id, pipeline), name=f"session:{pipeline.id}"
        )
        self._tasks[pipeline.id] = task
        self._ensure_stat(pipeline.id, receiver_id)
        self._stats[pipeline.id]["max_concurrency"] = max(1, int(pipeline.max_concurrency or 1))
        logger.info(
            "启动长驻会话 pipeline=%s receiver=%s max_concurrency=%s",
            pipeline.id, receiver_id, self._stats[pipeline.id]["max_concurrency"],
        )
        return True

    def stop(self, pipeline_id: str) -> None:
        task = self._tasks.pop(pipeline_id, None)
        if task and not task.done():
            task.cancel()
            logger.info("停止长驻会话 pipeline=%s", pipeline_id)
        # 会话停止后，processing 中的告警由租约超时自动回收
        self._inbox.requeue_expired() if self._inbox else None

    async def resume_all(
        self,
        get_pipeline: Callable[[str], PipelineDefinition | None],
    ) -> list[str]:
        """服务启动恢复：为所有持久化激活态的流水线拉起会话。"""
        started: list[str] = []
        if self._inbox is not None:
            try:
                self._inbox.requeue_expired()
            except Exception as e:
                logger.warning(f"启动回收过期租约失败: {e}")
        for pid in self._activation.get_all_active():
            try:
                pipeline = get_pipeline(pid) if get_pipeline else None
            except Exception:
                pipeline = None
            if pipeline is None:
                logger.warning("激活流水线 %s 定义未找到，无法恢复会话", pid)
                continue
            if self.ensure(pipeline):
                started.append(pid)
        if started:
            logger.info(f"恢复 {len(started)} 个长驻会话: {started}")
        return started

    async def stop_all(self) -> None:
        for pid in list(self._tasks):
            self.stop(pid)
        self._tasks.clear()

    # ------------------------------------------------------------------
    # Public — 查询
    # ------------------------------------------------------------------

    def is_running(self, pipeline_id: str) -> bool:
        task = self._tasks.get(pipeline_id)
        return bool(task and not task.done())

    def status(self, pipeline_id: str) -> dict[str, Any]:
        base = self._stats.get(pipeline_id, {})
        info = {
            "pipeline_id": pipeline_id,
            "active": self._activation.is_active(pipeline_id),
            "receiver_id": self._activation.get_binding(pipeline_id)
            or base.get("receiver_id", ""),
            "running": self.is_running(pipeline_id),
        }
        info.update({k: v for k, v in base.items() if k not in ("pipeline_id", "receiver_id")})
        # 实时积压深度
        if self._inbox is not None:
            try:
                st = self._inbox.stats(receiver_id=info["receiver_id"])
                info["backlog"] = st["backlog"]
                info["inbox"] = st
            except Exception:
                pass
        return info

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            self.status(pid) for pid in sorted(set(self._tasks) | set(self._stats))
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_receiver_id(pipeline: PipelineDefinition) -> str:
        """解析流水线绑定的接收器 id：激活绑定 > receiver 节点 agent > 节点 id。"""
        receiver_node = next(
            (n for n in pipeline.nodes if n.type == "receiver"), None
        )
        if receiver_node is None:
            return ""
        # 激活绑定（由 activate 请求显式提供）优先
        binding = get_activation_store().get_binding(pipeline.id)
        if binding:
            return binding
        return receiver_node.agent or receiver_node.id or ""

    # ------------------------------------------------------------------
    # Internal — 并行 Worker 池
    # ------------------------------------------------------------------

    def _ensure_stat(self, pipeline_id: str, receiver_id: str) -> dict[str, Any]:
        stat = self._stats.get(pipeline_id)
        if stat is None:
            stat = {
                "pipeline_id": pipeline_id,
                "receiver_id": receiver_id,
                "max_concurrency": 10,
                "started_at": time.time(),
                "processed": 0,
                "failed": 0,
                "dead": 0,
                "last_run_at": None,
                "last_error": "",
                "active": 0,
            }
            self._stats[pipeline_id] = stat
        return stat

    async def _loop(
        self, pipeline_id: str, receiver_id: str, pipeline: PipelineDefinition
    ) -> None:
        """长驻并行调度循环（每流水线一个）：

        维护一个 ≤ max_concurrency 的并发 Worker 集合；dispatcher 在有空位时
        从收件箱逐条 claim 并 spawn 独立 worker —— 每条入站数据 = 一次独立
        run / 独立对话。多 Worker 间互不阻塞，实现同流水线并行处理。
        """
        max_conc = max(1, int(pipeline.max_concurrency or 1))
        stat = self._ensure_stat(pipeline_id, receiver_id)
        stat["max_concurrency"] = max_conc
        pending: dict[int, asyncio.Task] = {}   # seq → worker task
        last_recover = 0.0

        def drop_done() -> None:
            for seq in [s for s, t in pending.items() if t.done()]:
                task = pending.pop(seq, None)
                if task is None:
                    continue
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    logger.warning("会话 worker pipeline=%s seq=%s 异常: %s", pipeline_id, seq, exc)

        try:
            while True:
                if not self._activation.is_active(pipeline_id):
                    break  # 被 deactivate 后退出（stop() 取消任务为兜底）
                if self._inbox is None:
                    await asyncio.sleep(_IDLE_SLEEP)
                    continue

                drop_done()
                now = time.time()
                if now - last_recover >= _RECOVER_EVERY:
                    try:
                        self._inbox.requeue_expired(receiver_id)
                    except Exception:
                        logger.exception(f"会话 {pipeline_id} 回收过期租约失败")
                    last_recover = now

                # 补满空闲并发位
                while len(pending) < max_conc:
                    try:
                        item = self._inbox.claim_next(receiver_id, lease_seconds=900.0)
                    except Exception:
                        logger.exception(f"会话 {pipeline_id} claim 收件箱失败")
                        await asyncio.sleep(_IDLE_SLEEP * 2)
                        break
                    if item is None:
                        break
                    seq = int(item.get("seq", 0))
                    task = asyncio.create_task(
                        self._run_one(pipeline_id, receiver_id, pipeline, item),
                        name=f"session:{pipeline_id}:seq:{seq}",
                    )
                    pending[seq] = task

                stat = self._ensure_stat(pipeline_id, receiver_id)
                stat["active"] = len(pending)

                if not pending:
                    await asyncio.sleep(_IDLE_SLEEP)
                    continue

                # 任一 Worker 完成即让出调度，继续补位消费
                done, _ = await asyncio.wait(
                    list(pending.values()), return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc and not isinstance(exc, asyncio.CancelledError):
                        logger.warning(
                            "会话 worker pipeline=%s 异常退出: %s", pipeline_id, exc,
                        )
        except asyncio.CancelledError:
            # 会话停止：取消全部在途 worker（处理中的收件箱条目由租约超时自动回收）
            for task in pending.values():
                task.cancel()
            if pending:
                await asyncio.gather(*pending.values(), return_exceptions=True)
            raise

    async def _run_one(
        self,
        pipeline_id: str,
        receiver_id: str,
        pipeline: PipelineDefinition,
        item: dict[str, Any],
    ) -> None:
        """单个入站条目的独立处理单元（一个 run / 一条流水线对话）。"""
        seq = int(item.get("seq", 0))
        fingerprint = str(item.get("fingerprint", ""))
        stat = self._ensure_stat(pipeline_id, receiver_id)
        stat["last_run_at"] = time.time()

        context: dict[str, Any] = {
            "source": "receiver_session",
            "_inbox_seq": seq,
            "_receiver_id": receiver_id,
            "alert_received_at": item.get("received_at", 0.0),
            "alert_fingerprint": fingerprint,
        }
        try:
            record = await self._runner.execute_one(
                pipeline,
                context,
                webhook="",
                dedup_key=f"alert:{fingerprint}" if fingerprint else "",
            )
            if record is None:
                raise RuntimeError("执行未产生结果记录")
            if record.status == "completed":
                self._inbox.complete(seq, run_id=record.run_id)
                stat["processed"] += 1
                stat["last_error"] = ""
            elif record.status == "paused":
                # auto 流水线不应含 confirm；异常暂停视为失败
                raise RuntimeError("自动化流水线进入暂停态（不应包含 confirm 节点）")
            else:
                # failed：收件箱计入重试/死信（run 已落库保留现场）
                err = record.error or "run failed"
                outcome = self._inbox.fail(seq, err)
                if outcome == "dead":
                    stat["dead"] += 1
                else:
                    stat["failed"] += 1
                stat["last_error"] = err[:500]
                logger.warning(
                    "入站研判失败 seq=%s pipeline=%s run=%s status=%s: %s",
                    seq, pipeline_id, record.run_id, record.status, err,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"会话 {pipeline_id} 处理 seq={seq} 异常")
            outcome = self._inbox.fail(seq, str(e))
            if outcome == "dead":
                stat["dead"] += 1
            else:
                stat["failed"] += 1
            stat["last_error"] = str(e)[:500]

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        await self.stop_all()


# ====================================================================
# 全局实例
# ====================================================================

_session: SessionSupervisor | None = None


def get_session_manager() -> SessionSupervisor:
    global _session
    if _session is None:
        _session = SessionSupervisor()
    return _session
