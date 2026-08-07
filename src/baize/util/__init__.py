"""Baize 工具包（独立实现）。"""

from __future__ import annotations


def create_system_prompt_renderer(instructions: str):
    """创建一个简易的系统提示词渲染器。

    支持 ``{context_variables}`` 占位符替换。
    """

    def render(context_variables: dict | None = None) -> str:
        ctx = context_variables or {}
        try:
            return instructions.format(**ctx)
        except (KeyError, ValueError):
            return instructions

    return render
