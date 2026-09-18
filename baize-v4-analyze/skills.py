"""Baize Skills —— 按工具名触发的按需技能加载（P6'）。

背景（P6 评审后的收敛方案）：
- 原 P6 方案让 Skill 加载依赖 P2 的意图路由，短期无法落地；
- P6' 改为更轻、今天就能用的触发方式：**按工具名触发** ——
  当模型在运行中调用某工具（或某前缀工具族）时，把对应的
  ``prompts/skills/<name>.md`` 技能文本注入到后续 LLM 上下文；
- 这样长 system prompt 里重复的"工具使用手册"段落可以抽成独立 skill
  文件（6 个红队 agent 尾部重复的 ``shared_browser_*`` 手册即试点），
  平时不再常驻每条请求，首次用到对应工具时才加载 —— token 节省实打实，
  且不依赖 P2 的图/意图结构。

文件约定：
- 技能正文存放于 ``src/baize/prompts/skills/*.md``；
- 本模块内 ``_TOOL_TRIGGERS`` 登记 工具名/前缀 -> skill 文件名
  （前缀以 ``*`` 结尾，如 ``shared_browser_*``）；
- ``resolve_skill_text(tool_name)`` 返回与工具匹配的技能文本，找不到返回 None。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_ROOT = Path(__file__).resolve().parent / "prompts"
_SKILLS_DIR = _PROMPTS_ROOT / "skills"

# 工具名/前缀 -> skill 文件名（前缀以 "*" 结尾，匹配工具名 startswith）。
# 后续新增技能时在此登记即可，无需改 Agent 逻辑。
_TOOL_TRIGGERS: dict[str, str] = {
    "shared_browser_*": "shared_browser_auth.md",
}


def registered_skill_names() -> list[str]:
    """返回所有已登记技能文件名（供测试与排障）。"""
    return sorted(set(_TOOL_TRIGGERS.values()))


def _load_skill_text(filename: str) -> str:
    path = _SKILLS_DIR / filename
    if not path.exists():
        logger.warning("技能文件缺失: %s", path)
        return ""
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=64)
def _resolve_skill_text_cached(tool_name: str) -> str:
    """按工具名解析技能文本（带缓存，幂等、线程安全）。"""
    for trigger, filename in _TOOL_TRIGGERS.items():
        if trigger.endswith("*"):
            prefix = trigger[:-1]
            matched = tool_name.startswith(prefix)
        else:
            matched = tool_name == trigger
        if matched:
            text = _load_skill_text(filename)
            if text:
                logger.debug("技能触发: tool=%s -> %s", tool_name, filename)
                return text
    return ""


def resolve_skill_text(tool_name: str) -> str:
    """返回与指定工具匹配的技能文本；无匹配返回空串。

    Args:
        tool_name: 本次被调用/计划调用的工具名。
    """
    if not tool_name:
        return ""
    return _resolve_skill_text_cached(tool_name)


def clear_skill_cache() -> None:
    """清空技能解析缓存（供测试使用）。"""
    _resolve_skill_text_cached.cache_clear()
