"""Episodic Memory：把一次 Agent 任务固化为 Episode 快照。

Episode 完整保留：task / target / 起止时间 / Agent 动作序列 / 每一步工具调用
（名称、参数、输出）/ 观察 / 结论 / 结果。steps 是带编号的时序数组，
证据链（EvidenceRef）可通过 `ref`（如 `t:3`、`o:2`、`result`）精确回溯到片段。

注意：episode 是「原始执行轨迹」，落库后不被修改（只追加），以状态标记结局。
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from .models import EpisodeRecord
from .utils import excerpt, new_id, now_iso

logger = logging.getLogger("baize.memory.episode")

# 单段轨迹的最大保存长度（episode 保留足够细节，但避免被巨型输出撑爆）
MAX_ARGS = 4000
MAX_OUTPUT = 12000
MAX_STEP_TEXT = 2000


class EpisodeBuilder:
    """从执行流顺序构造 Episode（不感知上游具体消息结构）。

    喂入的是已规整好的“事件行”，两种均可：
      {"type": "tool_call", "name": "nmap", "arguments": "...", "output": "..."}
      {"type": "action"/"decision"/"observation"/"result"/"error",
       "text": "..."，可选 "role": "user|assistant"}
    """

    def __init__(self, *, task: str = "", target: str = "",
                 agent_key: str = "", session_id: str = "",
                 start_at: str = "", task_id: Optional[str] = None):
        self.task = (task or "").strip()
        self.target = (target or "").strip()
        self.agent_key = agent_key
        self.session_id = session_id
        self.start_at = start_at or now_iso()
        self.id = task_id or new_id("ep")
        self.steps: list[dict] = []
        self._counter: dict[str, int] = {}

    def _next_ref(self, kind: str) -> str:
        self._counter[kind] = self._counter.get(kind, 0) + 1
        return f"{kind}:{self._counter[kind]}"

    def add_tool_call(self, name: str = "", arguments: str = "",
                      output: str = "", status: str = "", ts: str = "") -> str:
        ref = self._next_ref("t")
        self.steps.append({
            "id": ref,
            "type": "tool_call",
            "tool_name": (name or "").strip(),
            "arguments": excerpt(arguments, MAX_ARGS),
            "output": excerpt(output, MAX_OUTPUT),
            "status": status or "",
            "ts": ts or now_iso(),
        })
        return ref

    def add_text(self, kind: str, text: str, role: str = "",
                 ts: str = "") -> str:
        """kind: action/decision/observation/result/error。"""
        ref = self._next_ref("o" if kind == "observation" else kind[0])
        self.steps.append({
            "id": ref,
            "type": kind,
            "role": role,
            "text": excerpt(text, MAX_STEP_TEXT),
            "ts": ts or now_iso(),
        })
        return ref

    def conclude(self, *, result_text: str = "", error_text: str = "",
                 status: str = "success", summary: str = "") -> EpisodeRecord:
        """封口 Episode：补 result/error 与结局状态。"""
        if result_text:
            self.add_text("result", result_text, role="assistant")
        if error_text:
            self.add_text("error", error_text, role="assistant")
        if status not in ("success", "failed", "interrupted", "discarded"):
            status = "success"
        return EpisodeRecord(
            id=self.id,
            task=self.task,
            target=self.target,
            agent_key=self.agent_key,
            session_id=self.session_id,
            start_at=self.start_at,
            end_at=now_iso(),
            status=status,
            steps=self.steps,
            result_text=result_text,
            error_text=error_text,
            summary=summary or self._auto_summary(),
            messages_count=len(self.steps),
        )

    def _auto_summary(self) -> str:
        """无 LLM 的摘要：目标 + 用到的工具 + 结论/错误首句。"""
        tools = [s.get("tool_name", "") for s in self.steps
                 if s.get("type") == "tool_call" and s.get("tool_name")]
        unique = list(dict.fromkeys(tools))[:5]
        tail = (self.steps[-1].get("text", "")[:120] if self.steps else "")
        head = self.task[:80]
        return f"目标:{head} 工具:{'/'.join(unique) or '—'} 结尾:{tail}"[:300]


def build_from_log(task: str = "", target: str = "",
                   events: Iterable[dict] | None = None,
                   result_text: str = "", error_text: str = "",
                   status: str = "success", agent_key: str = "",
                   session_id: str = "", start_at: str = "",
                   task_id: Optional[str] = None) -> EpisodeRecord:
    """便捷入口：从规整好的事件流直接产出 EpisodeRecord。"""
    builder = EpisodeBuilder(task=task, target=target, agent_key=agent_key,
                             session_id=session_id, start_at=start_at,
                             task_id=task_id)
    for ev in events or []:
        etype = (ev.get("type") or "").strip()
        if etype == "tool_call" or ev.get("name"):
            builder.add_tool_call(
                name=ev.get("name", ev.get("tool_name", "")),
                arguments=ev.get("arguments", ev.get("args", "")),
                output=ev.get("output", ""),
                status=ev.get("status", ""),
                ts=ev.get("ts", ev.get("timestamp", "")),
            )
        elif etype and ev.get("text") is not None:
            builder.add_text(etype, ev.get("text", ""),
                             role=ev.get("role", ""), ts=ev.get("ts", ""))
    return builder.conclude(result_text=result_text, error_text=error_text,
                            status=status)
