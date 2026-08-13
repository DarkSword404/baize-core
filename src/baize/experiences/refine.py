"""经验提炼：信号检测 + LLM 复盘总结生成。

闭环：
1. 每轮对话完成时（或新一轮开始前），用纯规则信号判断是否值得提炼；
2. 值得提炼时，从会话轨迹中提取"尝试过程"（工具调用 + 最终结论）；
3. 调用 LLM 生成复盘总结候选（title / content / tags）；
4. 候选挂到会话，由前端展示卡片，用户确认/编辑后入库（混合模式）。
"""

from __future__ import annotations

import json
from typing import Optional

from .store import ExperienceItem, new_id, now_iso

# 结论性信号关键词：命中即认为本轮有明确结果，值得沉淀
CONCLUSION_KEYWORDS = [
    "漏洞", "发现", "利用", "成功", "拿下", "getshell", "shell", "flag",
    "vuln", "vulnerability", "exploit", "found", "success", "pwned",
    "弱口令", "注入", "上传", "绕过", "提权", "权限", "拿下", "渗透",
    "扫描", "指纹", "版本", "cve-", "msf", "nmap", "sqlmap",
]

# 失败信号关键词
FAILURE_KEYWORDS = [
    "失败", "错误", "无结果", "超时", "拒绝", "无法", "不能", "不通",
    "failed", "error", "timeout", "refused", "denied", "unable", "no result",
]


def _has_any(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def detect_turn_signals(
    tool_events: list[dict],
    final_text: str,
    prior_turns_text: str = "",
    user_message: str = "",
) -> dict:
    """纯规则评估本轮是否值得提炼。

    返回 {should_refine, reason}。信号来源：
    - 本轮工具链出现过错误（绕路）但最终给出结论 → 教训价值高；
    - 最终结论含明确结果关键词；
    - 用户消息带纠正/指导/补充（如"应该用""试试""先"等）；
    - 上一轮无结论而本轮成功（用户辅助后成功）。
    """
    reasons: list[str] = []

    tool_text = " ".join(
        f"{ev.get('name', '')} {ev.get('arguments', '')} {ev.get('output', '')}"
        for ev in tool_events
    )
    failed_once = _has_any(tool_text, FAILURE_KEYWORDS) or _has_any(final_text, FAILURE_KEYWORDS)
    concluded = _has_any(final_text, CONCLUSION_KEYWORDS)

    if failed_once and concluded:
        reasons.append("工具链中出现错误后最终走通，弯路经验值得沉淀")
    elif concluded:
        reasons.append("本轮任务有明确结论")

    # 用户指导信号（下一轮判定时由 prior 提供）
    guidance_keywords = ["应该", "可以试", "试试", "用这个", "换一个", "不要", "别用", "记得", "先"]
    if _has_any(user_message, guidance_keywords):
        reasons.append("用户消息含纠正/指导，可能蕴含经验")

    # 上一轮失败、本轮成功 → 用户辅助后成功（最高价值）
    if prior_turns_text and _has_any(prior_turns_text, FAILURE_KEYWORDS) and concluded:
        reasons.append("用户辅助后成功，对比路径值得沉淀")

    return {
        "should_refine": bool(reasons),
        "reasons": reasons,
        "concluded": concluded,
        "failed_once": failed_once,
    }


def build_context_material(
    user_message: str,
    final_text: str,
    tool_events: list[dict],
    prior_history: list[dict] | None = None,
    max_chars: int = 6000,
) -> str:
    """提取本轮尝试轨迹文本，作为 LLM 提炼的输入素材。"""
    parts: list[str] = []
    if prior_history:
        recent = prior_history[-6:]
        for m in recent:
            role = m.get("role", "")
            content = str(m.get("content", ""))[:800]
            if role in ("user", "assistant") and content:
                parts.append(f"[{role}] {content}")
    parts.append(f"[user] {user_message[:1000]}")
    for ev in tool_events:
        name = ev.get("name", "")
        args = str(ev.get("arguments", ""))[:500]
        out = str(ev.get("output", ""))[:1000]
        parts.append(f"[tool:{name}] args={args}\noutput={out}")
    if final_text.strip():
        parts.append(f"[assistant] {final_text[:2000]}")
    material = "\n".join(parts)
    return material[:max_chars]


async def refine_experience(
    client,
    agent_key: str,
    session_id: str,
    user_message: str,
    final_text: str,
    tool_events: list[dict],
    prior_history: list[dict] | None = None,
    scope: str = "auto",
) -> dict:
    """调用 LLM 生成复盘总结候选条目。

    返回 dict（可直接转 ExperienceItem）：{title, content, tags, scope}
    """
    material = build_context_material(user_message, final_text, tool_events, prior_history)

    system_prompt = (
        "你是一名资深渗透测试专家，负责把一次渗透测试过程沉淀为可复用的经验。\n"
        "给定一段尝试轨迹（用户提问、工具调用、输出、最终结论），请复盘提炼。\n"
        "要求：\n"
        "1. title：一句话概括这条经验（如 'WordPress 打点套路'）\n"
        "2. content：复盘总结，包含『教训/踩坑点』与『可复用的步骤或技巧』和『适用条件（如目标指纹、版本特征）』，"
        "用简洁的中文条目式表述，200 字以内\n"
        "3. tags：3-6 个检索标签（技术关键词，如 ['wordpress','wpscan','指纹识别']）\n"
        "只输出 JSON：{\"title\": \"...\", \"content\": \"...\", \"tags\": [...]}"
    )

    try:
        from baize.sdk.client import ChatMessage

        result = await client.complete(
            [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=material),
            ]
        )
        text = result.content or ""
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return _fallback_candidate(agent_key, session_id, text)
        parsed = json.loads(text[start : end + 1])
        title = str(parsed.get("title", "")).strip() or "渗透测试经验"
        content = str(parsed.get("content", "")).strip()
        tags = [str(t).strip() for t in parsed.get("tags", []) if str(t).strip()][:8]
        resolved_scope = scope if scope in ("global",) else f"agent:{agent_key}"
        return {
            "title": title,
            "content": content,
            "tags": tags,
            "scope": resolved_scope,
            "_raw": text,
        }
    except Exception:  # noqa: BLE001
        return _fallback_candidate(agent_key, session_id, "")


def _fallback_candidate(agent_key: str, session_id: str, raw: str) -> dict:
    return {
        "title": "渗透测试经验（待编辑）",
        "content": raw.strip() or "（LLM 提炼失败，请手动填写）",
        "tags": [],
        "scope": f"agent:{agent_key}",
        "_raw": "",
    }


def candidate_to_item(candidate: dict, agent_key: str, session_id: str) -> ExperienceItem:
    """将候选 dict 转为正式经验条目（用户确认后调用）。"""
    return ExperienceItem(
        id=new_id(),
        scope=candidate.get("scope") or f"agent:{agent_key}",
        title=candidate.get("title") or "渗透测试经验",
        content=candidate.get("content") or "",
        tags=candidate.get("tags") or [],
        source_session_id=session_id,
        source_agent=agent_key,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
