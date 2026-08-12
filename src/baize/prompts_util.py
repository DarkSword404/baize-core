"""
Baize Prompt Template Loader — 从 Markdown 文件加载 system prompt 模板。
兼容 CAI 的 load_prompt_template 模式。
"""
import os
from pathlib import Path
from functools import lru_cache

_PROMPTS_ROOT = Path(__file__).parent / "prompts"


def load_prompt_template(template_name: str) -> str:
    """
    从 prompts/ 目录加载 .md 模板并返回原始内容。

    参数:
        template_name: 文件名或相对路径，如 "system_red_team_agent.md"
                      或 "prompts/system_red_team_agent.md"

    返回: 模板字符串（不做任何渲染或变量替换）
    """
    # 统一处理路径：去掉可能的 "prompts/" 前缀
    name = template_name
    if name.startswith("prompts/"):
        name = name[len("prompts/"):]
    path = _PROMPTS_ROOT / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt 模板未找到: {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=64)
def get_agent_instructions(agent_key: str) -> str:
    """
    根据 agent key 获取对应 system prompt 指令。
    约定: prompt 文件名为 prompts/system_{key}.md
    """
    filename = f"system_{agent_key}.md"
    return load_prompt_template(filename)


def get_agentbuilder_instructions() -> str:
    """Agent Builder 的专用 instructions — 用于创建新智能体。"""
    return load_prompt_template("system_agent_builder.md")
