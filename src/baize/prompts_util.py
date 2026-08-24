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


# 判定某行是否为 "Baize layering 注入噪声行" — 这些行不属于智能体原始标题/简介
_LAYER_NOISE_PREFIX_MARKDOWN = (
    "**Baize layering:**",
    "**This file**",
    "**This document**",
    "**This persona**",
    "**This code**",
    "## ",
)
_LAYER_NOISE_MARKERS = (
    ("Baize prepends", "micro-profile"),
    ("Baize layering", "micro-profile"),
)


def _is_noise_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    for prefix in _LAYER_NOISE_PREFIX_MARKDOWN:
        if s.startswith(prefix):
            return True
    for (a, b) in _LAYER_NOISE_MARKERS:
        if a in s and b in s:
            return True
    # XML tag 行(如 <core_identity>、<safety_guardrails>)不属于标题/简介
    if len(s) >= 2 and s[0] == "<" and s[-1] == ">":
        return True
    return False


def _beautify_fallback(key: str) -> str:
    return key.replace("_", " ").strip().title()


def extract_display_name_and_desc(instructions: str, fallback_key: str) -> tuple[str, str]:
    """从完整的智能体 system prompt 中提取适合前端卡片展示的短名称 + 简介。

    修复历史问题：Baize layering 头部模板可能被注入在 prompt 最前面若干行,
    原来直接取第 0/1 行会把 layering 注入的长句当成标题,导致前端智能体页面
    出现 `**Baize layering:** When enabled, Baize prepends a global cyber baseline and ...`
    这种超长乱码标题,并显示「无描述」。

    策略:
    1) 优先找 Markdown H1 行(第 1 个 `# Title`),这是作者明示的 display name;
    2) 若无 H1,则在前 40 行里跳过所有 layering 噪声行,取前 2 条有效句子作 name/desc;
    3) 若结果超长/为空,回退为 fallback_key(文件名)的美化形式。
    """
    if not instructions:
        return _beautify_fallback(fallback_key), ""
    lines = instructions.splitlines()
    probe = lines[:40]

    # Phase 1 — 找 H1 Markdown 标题
    h1_idx = -1
    for i, raw in enumerate(probe):
        s = raw.strip()
        if s.startswith("# ") and not s.startswith("## "):
            h1_idx = i
            break
    if h1_idx >= 0:
        name = probe[h1_idx][len("# "):].strip().strip("*").strip()
        desc = ""
        for j in range(h1_idx + 1, len(probe)):
            line = probe[j].strip()
            if line and not _is_noise_line(line):
                desc = line.strip("*").strip()
                break
        return _finalize_name_desc(name, desc, fallback_key)

    # Phase 2 — 无 H1,扫前 40 行取非噪声的前 2 条有效句子
    clean: list[str] = []
    for raw in probe:
        s = raw.strip().strip("*").strip()
        if s and not _is_noise_line(s):
            clean.append(s)
        if len(clean) >= 2:
            break
    name = clean[0] if clean else ""
    desc = clean[1] if len(clean) > 1 else ""
    return _finalize_name_desc(name, desc, fallback_key)


def _finalize_name_desc(name: str, desc: str, fallback_key: str) -> tuple[str, str]:
    fb = _beautify_fallback(fallback_key)
    name = (name or "").strip().strip("*").strip()
    desc = (desc or "").strip().strip("*").strip()
    # 启发式:明显属于“Execution pattern(执行模板行)”而不是标题的关键词 → 直接走 fallback
    template_markers = ("→ adapt", "OWASP LLM", "hypothesis", "Plan → act",
                        "Filter → extract", "Triage hypothesis", "Hypothesis → disassemble",
                        "Attach → read", "Plan capture", "Plan frequency", "Classify (meta")
    is_template_row = any(m in name for m in template_markers)
    if (
        not name
        or len(name) > 100
        or "Baize layering" in name
        or is_template_row
    ):
        name = fb
    if len(desc) > 300:
        desc = desc[:297].rstrip() + "..."
    return name, desc
