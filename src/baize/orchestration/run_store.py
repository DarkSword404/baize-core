"""
执行记录持久化存储 — 内存实现（生产可替换为 SQLite/Postgres）。

存储每次流水线执行的生命周期记录和事件历史。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

JobStatus = Literal["pending", "running", "completed", "failed", "paused"]


class RunRecord:
    """一次流水线执行记录。"""

    def __init__(
        self,
        run_id: str,
        pipeline_id: str,
        pipe_type: str,
        status: JobStatus = "pending",
    ):
        self.run_id = run_id
        self.pipeline_id = pipeline_id
        self.pipe_type = pipe_type
        self.status: JobStatus = status
        self.context: dict[str, Any] = {}
        self.webhook: str = ""
        self.created_at = time.time()
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.error: str = ""

        # 节点执行记录
        self.nodes: dict[str, dict[str, Any]] = {}

        # 事件历史（供重连时补齐）
        self.events: list[dict[str, Any]] = []

        # 最终输出
        self.report: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "pipe_type": self.pipe_type,
            "status": self.status,
            "context": self.context,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "nodes": self.nodes,
            "events": self.events,
            "events_count": len(self.events),
            "report": self.report,
        }

    def brief(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "pipe_type": self.pipe_type,
            "status": self.status,
            "created_at": self.created_at,
            "error": self.error,
        }


class RunStore:
    """运行记录仓库 — 线程安全的内存存储。

    生产环境可替换为 SQLiteRunStore / PostgresRunStore，
    实现相同接口即可。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._records: dict[str, RunRecord] = {}

    def create(self, record: RunRecord) -> None:
        with self._lock:
            self._records[record.run_id] = record

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def update_status(self, run_id: str, status: JobStatus, error: str = "") -> None:
        with self._lock:
            r = self._records.get(run_id)
            if r:
                r.status = status
                if error:
                    r.error = error
                if status == "running" and r.started_at is None:
                    r.started_at = time.time()
                if status in ("completed", "failed") and r.ended_at is None:
                    r.ended_at = time.time()

    def add_event(self, run_id: str, event: dict[str, Any]) -> None:
        """追加事件到历史列表。"""
        with self._lock:
            r = self._records.get(run_id)
            if r:
                r.events.append(event)

    def add_node_record(self, run_id: str, node_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            r = self._records.get(run_id)
            if r:
                r.nodes[node_id] = record

    def set_final_state(self, run_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            r = self._records.get(run_id)
            if r:
                final_status = state.get("status", "completed")
                r.status = final_status
                r.nodes = state.get("nodes", r.nodes)
                r.report = state.get("report", "")
                r.error = state.get("error", "")
                if r.ended_at is None:
                    r.ended_at = time.time()

    def list_runs(
        self,
        pipeline_id: str | None = None,
        status: JobStatus | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出运行记录（按创建时间倒序）。"""
        with self._lock:
            records = list(self._records.values())

        if pipeline_id:
            records = [r for r in records if r.pipeline_id == pipeline_id]
        if status:
            records = [r for r in records if r.status == status]

        records.sort(key=lambda r: r.created_at, reverse=True)
        return [r.brief() for r in records[:limit]]

    def get_events_since(self, run_id: str, last_event_id: str = "") -> list[dict[str, Any]]:
        """获取指定事件之后的增量事件（用于重连补齐）。"""
        with self._lock:
            r = self._records.get(run_id)
            if not r:
                return []
            if last_event_id:
                try:
                    idx = next(
                        i for i, e in enumerate(r.events)
                        if e.get("event_id") == last_event_id
                    )
                    return r.events[idx + 1:]
                except StopIteration:
                    return []
            return r.events


# ====================================================================
# 流水线激活状态管理
# ====================================================================

class PipelineActivationStore:
    """自动化流水线激活状态管理。

    auto 类型的流水线创建后默认关闭，需要用户手动开启后才开始接收数据。
    manual 类型的流水线始终处于可用状态，供对话时选择。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._activations: dict[str, bool] = {}  # pipeline_id -> is_active

    def is_active(self, pipeline_id: str) -> bool:
        with self._lock:
            return self._activations.get(pipeline_id, False)

    def activate(self, pipeline_id: str) -> None:
        with self._lock:
            self._activations[pipeline_id] = True

    def deactivate(self, pipeline_id: str) -> None:
        with self._lock:
            self._activations[pipeline_id] = False

    def get_all_active(self) -> list[str]:
        with self._lock:
            return [pid for pid, active in self._activations.items() if active]

    def get_status(self, pipeline_id: str) -> dict[str, Any]:
        return {
            "pipeline_id": pipeline_id,
            "active": self.is_active(pipeline_id),
        }


# ====================================================================
# 全局实例
# ====================================================================

_default_store = RunStore()
_default_activation_store = PipelineActivationStore()


def get_run_store() -> RunStore:
    return _default_store


def get_activation_store() -> PipelineActivationStore:
    return _default_activation_store
