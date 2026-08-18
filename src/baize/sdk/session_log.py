"""Append-only 会话日志（Session Log）—— 会话的单一事实源。

对齐 deepseek-harness 的 Session Log 设计：

- **单一事实源**：所有会话事件（用户消息 / 模型请求 / 工具调用 / 工具结果 /
  回合开始结束）按序追加，不可修改、不可删除。
- **可重建**：模型历史（``derive_messages``）从日志投影生成，
  任何到达模型请求的内容必须能从日志重建 —— 这是审计与回放的基础。
- **审计取证**：一次攻击链可完整重放（``replay``），满足安全场景的
  合规审计 / DFIR 取证需求。
- **持久化**：可选 JSONL 落盘（append-only 写入，天然支持增量）。

事件 kind 约定:
- ``session/start`` 会话开始
- ``user/message`` 用户消息
- ``agent/request`` 发给模型的请求（messages 快照或摘要）
- ``agent/response`` 模型原始响应（content / tool_calls）
- ``tool/call`` 工具调用（name + arguments）
- ``tool/result`` 工具结果（name + output + duration）
- ``turn/start`` / ``turn/end`` 回合边界
- ``session/end`` 会话结束
- ``system`` 系统事件（配置变更 / 策略拦截等）
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator, Optional

_SESSION_EVENT_KINDS = {
    "session/start",
    "user/message",
    "agent/request",
    "agent/response",
    "tool/call",
    "tool/result",
    "turn/start",
    "turn/end",
    "session/end",
    "system",
}


@dataclass
class SessionEvent:
    """一条会话日志事件（append-only，不可变）。"""

    seq: int
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "kind": self.kind, "payload": self.payload, "ts": self.ts, "id": self.id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionEvent":
        return cls(
            seq=int(data["seq"]),
            kind=str(data["kind"]),
            payload=data.get("payload", {}),
            ts=float(data.get("ts", 0.0)),
            id=str(data.get("id", "")),
        )


class SessionLog:
    """Append-only 会话日志。

    用法::

        log = SessionLog(session_id="abc", path="/var/log/baize/abc.jsonl")
        log.append("user/message", content="扫描 127.0.0.1")
        log.append("tool/call", name="port_scan", arguments={"target": "127.0.0.1"})
        messages = log.derive_messages()   # 从日志投影模型历史
        for line in log.replay():          # 人类可读审计重放
            print(line)
    """

    def __init__(self, session_id: Optional[str] = None, path: Optional[str] = None) -> None:
        self.session_id = session_id or uuid.uuid4().hex
        self.path = path
        self._events: list[SessionEvent] = []
        self._seq = 0
        if path and os.path.exists(path):
            self._load_existing()

    # ------------------------------------------------------------------
    # 追加（唯一写入入口）
    # ------------------------------------------------------------------

    def append(self, kind: str, **payload: Any) -> SessionEvent:
        if kind not in _SESSION_EVENT_KINDS:
            raise ValueError(f"未知事件类型: {kind!r}，允许: {sorted(_SESSION_EVENT_KINDS)}")
        event = SessionEvent(seq=self._seq, kind=kind, payload=payload)
        self._seq += 1
        self._events.append(event)
        if self.path:
            self._append_to_file(event)
        return event

    def _append_to_file(self, event: SessionEvent) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def _load_existing(self) -> None:
        """从既有 JSONL 恢复事件（只读加载，追加继续）。"""
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = SessionEvent.from_dict(json.loads(line))
                    self._events.append(event)
                    if event.seq >= self._seq:
                        self._seq = event.seq + 1
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    # ------------------------------------------------------------------
    # 查询 / 投影
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[SessionEvent]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    @property
    def events(self) -> list[SessionEvent]:
        return list(self._events)

    def filter(self, kind: Optional[str] = None) -> list[SessionEvent]:
        if kind is None:
            return self.events
        return [e for e in self._events if e.kind == kind]

    def to_dicts(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._events]

    # ------------------------------------------------------------------
    # 模型历史投影（单一事实源）
    # ------------------------------------------------------------------

    def derive_messages(self) -> list[dict[str, Any]]:
        """从日志投影模型可见的历史消息（OpenAI chat 格式）。

        - ``user/message`` -> role=user
        - ``agent/response`` 含 content 且无 tool_calls -> role=assistant
        - ``agent/response`` 含 tool_calls -> role=assistant + tool_calls
        - ``tool/result`` -> role=tool（携带 tool_call_id）
        """
        messages: list[dict[str, Any]] = []
        tool_map: dict[str, str] = {}  # tool_call_id -> tool name（用于 tool/result 对齐）

        for ev in self._events:
            p = ev.payload
            if ev.kind == "user/message":
                messages.append({"role": "user", "content": p.get("content", "")})
            elif ev.kind == "agent/response":
                content = p.get("content") or ""
                tool_calls = p.get("tool_calls")
                if tool_calls:
                    msg: dict[str, Any] = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
                    messages.append(msg)
                    for tc in tool_calls:
                        if isinstance(tc, dict) and tc.get("id"):
                            tool_map[tc["id"]] = tc.get("function", {}).get("name", "?")
                else:
                    messages.append({"role": "assistant", "content": content})
            elif ev.kind == "tool/result":
                tc_id = p.get("tool_call_id") or p.get("call_id")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id or "?",
                        "content": p.get("output", ""),
                    }
                )
        return messages

    # ------------------------------------------------------------------
    # 审计重放
    # ------------------------------------------------------------------

    def replay(self) -> list[str]:
        """生成人类可读的审计回放（按序），可直接写入报告 / 日志。"""
        lines: list[str] = []
        for ev in self._events:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.ts))
            p = ev.payload
            if ev.kind == "session/start":
                lines.append(f"[{ts}] 会话开始 {self.session_id}")
            elif ev.kind == "user/message":
                lines.append(f"[{ts}] 用户: {p.get('content', '')}")
            elif ev.kind == "agent/request":
                lines.append(f"[{ts}] 模型请求: {p.get('model', '')} messages={p.get('message_count', len(p.get('messages', [])))}")
            elif ev.kind == "agent/response":
                lines.append(f"[{ts}] 模型回复: {p.get('content', '') or '(tool calls)'}")
            elif ev.kind == "tool/call":
                lines.append(f"[{ts}] 工具调用: {p.get('name')}({p.get('arguments', {})})")
            elif ev.kind == "tool/result":
                lines.append(f"[{ts}] 工具结果[{p.get('name')}] ({p.get('duration', 0):.2f}s): {p.get('output', '')}")
            elif ev.kind == "turn/start":
                lines.append(f"[{ts}] --- 回合开始 #{p.get('index', '?')} ---")
            elif ev.kind == "turn/end":
                lines.append(f"[{ts}] --- 回合结束 #{p.get('index', '?')} ---")
            elif ev.kind == "session/end":
                lines.append(f"[{ts}] 会话结束（reason={p.get('reason', 'normal')}）")
            elif ev.kind == "system":
                lines.append(f"[{ts}] 系统: {p.get('message', '')}")
        return lines

    def to_report(self) -> str:
        """完整审计报告（多行文本）。"""
        return "\n".join(self.replay())

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """一次性导出全部事件到 JSONL。"""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for ev in self._events:
                fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: str, session_id: Optional[str] = None) -> "SessionLog":
        log = cls(session_id=session_id, path=path)
        return log


__all__ = ["SessionLog", "SessionEvent"]
