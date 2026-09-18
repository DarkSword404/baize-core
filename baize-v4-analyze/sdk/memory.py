"""Baize 记忆抽象（Memory）。

对齐 LangChain 的 Memory 概念，但保持轻量：
- ``BaseMemory``：统一接口（``load`` / ``save`` / ``clear``）。
- ``InMemoryMemory``：进程内默认实现（按 session_id 存取），
  适合演示与单进程部署；持久化记忆可继承 ``BaseMemory`` 自行实现
  （如 SQLite / Redis / 文件），Agent 无需改动。
"""

from __future__ import annotations

import abc
from typing import Any, Optional


class BaseMemory(abc.ABC):
    """记忆抽象基类。

    实现 ``load`` 与 ``save`` 即可接入 Agent 的记忆注入：

    - ``load`` 返回一段"记忆文本"，Agent 会在每次运行时将其注入
      system 指令（位于系统指令之后、对话之前），供模型参考历史结论。
    - ``save`` 在每次 Agent 运行结束后被调用，可保存对话历史 /
      运行时状态 / 经验复盘等。

    ``session_id`` 用于区分不同的会话（多用户 / 多任务隔离）。
    """

    @abc.abstractmethod
    def load(self, session_id: Optional[str] = None) -> str:
        """加载记忆文本（为空串表示无记忆）。"""

    @abc.abstractmethod
    def save(
        self,
        messages: list[Any],
        session_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """保存对话历史与附加状态。"""

    def clear(self, session_id: Optional[str] = None) -> None:
        """清空指定会话的记忆。"""


class InMemoryMemory(BaseMemory):
    """进程内记忆（默认实现）。

    ``session_id`` 缺省时使用 "default" 会话。
    每次 ``save`` 覆盖该会话的文本（保留最新一次运行摘要）。
    """

    def __init__(self, max_chars: int = 8000) -> None:
        self._store: dict[str, str] = {}
        self.max_chars = max_chars

    def load(self, session_id: Optional[str] = None) -> str:
        return self._store.get(session_id or "default", "")

    def save(
        self,
        messages: list[Any],
        session_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """把对话历史压缩为可注入的记忆文本。

        仅保留 user 提问与最终 assistant 回复（工具过程省略），
        并截断到 ``max_chars``，避免记忆块撑爆上下文。
        """
        lines: list[str] = []
        final_reply = ""
        for m in messages:
            role = getattr(m, "role", "")
            content = getattr(m, "content", "") or ""
            if role == "user":
                if content and not content.startswith("（注意：") and not content.startswith("请继续回答"):
                    lines.append(f"用户: {content[:200]}")
            elif role == "assistant" and not getattr(m, "tool_calls", None):
                if content:
                    final_reply = content
        if final_reply:
            lines.append(f"助手: {final_reply[:400]}")
        text = "\n".join(lines)
        if len(text) > self.max_chars:
            text = text[: self.max_chars]
        self._store[session_id or "default"] = text

    def clear(self, session_id: Optional[str] = None) -> None:
        self._store.pop(session_id or "default", None)


__all__ = ["BaseMemory", "InMemoryMemory"]
