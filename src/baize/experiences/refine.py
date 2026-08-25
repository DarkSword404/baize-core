"""经验提炼：信号检测 + LLM 复盘总结生成 + 自动入库（Skill 自我改进）。

闭环（参考 Hermes Agent 的 closed learning loop）：
1. 每轮对话完成时，用纯规则信号判断是否值得提炼；
2. 值得提炼时，从会话轨迹中提取"尝试过程"（工具调用 + 最终结论）；
3. 调用 LLM 生成复盘总结候选（title / content / tags / confidence）；
4. 高置信度（≥ 0.7）自动入库，低置信度挂到前端由用户确认（混合模式）。
5. 入库后自动更新 embedding 向量，使下次检索时即可命中。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .store import ExperienceItem, new_id, now_iso

logger = logging.getLogger("baize.refine")

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

# 自动入库的最低置信度阈值（越高越保守）
_AUTO_SAVE_CONFIDENCE = 0.70


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

    # 用户指导信号（下一轮判定时由 prior 提供）。
    # 仅当本轮确有产出（final_text 非空）时才提示提炼，
    # 避免模型空转/空回复时仅凭用户措辞就触发经验提示。
    guidance_keywords = ["应该", "可以试", "试试", "用这个", "换一个", "不要", "别用", "记得", "先"]
    if final_text.strip() and _has_any(user_message, guidance_keywords):
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


# ---- 自动提炼（Skill 自我改进）-----------------------------------------------

_REFINE_CONFIDENCE_PROMPT = (
    "你是一名资深渗透测试专家。请评估以下经验提炼候选的质量。\n"
    "候选标题：{title}\n候选内容：{content}\n候选标签：{tags}\n\n"
    "评分标准：\n"
    "- 0.9-1.0：可复用性强，步骤清晰，适用条件明确，tags 准确\n"
    "- 0.7-0.89：有参考价值，但步骤或条件不够清晰\n"
    "- 0.5-0.69：有部分参考价值，信息不够完整\n"
    "- 0.0-0.49：价值低或内容混乱\n\n"
    "只输出一个 0.0 到 1.0 之间的置信度数字，如 0.85"
)


async def _score_confidence(client, candidate: dict) -> float:
    """用 LLM 评估候选经验的质量置信度。"""
    try:
        from baize.sdk.client import ChatMessage

        prompt = _REFINE_CONFIDENCE_PROMPT.format(
            title=candidate.get("title", ""),
            content=candidate.get("content", ""),
            tags=candidate.get("tags", []),
        )
        result = await client.complete(
            [ChatMessage(role="user", content=prompt)],
            tools=None,
        )
        text = (result.content or "").strip()
        # 提取浮点数
        import re as _re
        match = _re.search(r"([01](?:\.\d+)?)", text)
        if match:
            return float(match.group(1))
        return 0.5
    except Exception:
        return 0.5


async def auto_refine_if_worthy(
    client,
    agent_key: str,
    session_id: str,
    user_message: str,
    final_text: str,
    tool_events: list[dict],
    prior_history: list[dict] | None = None,
    scope: str = "auto",
    store=None,
) -> dict:
    """自动提炼经验：信号检测 → LLM 提炼 → 置信度评分 → 高置信度自动入库。

    返回:
        {
            "auto_saved": bool,      # 是否已自动入库
            "candidate": dict,        # 提炼候选
            "confidence": float,      # 置信度
            "item_id": str | None,    # 入库后的条目 ID
        }

    参考 Hermes Agent 的 closed learning loop：任务完成后自动创建 Skill，
    高置信度直接入库，无须人工确认。
    """
    # 1. 信号检测
    signals = detect_turn_signals(tool_events, final_text, "", user_message)
    if not signals["should_refine"]:
        return {"auto_saved": False, "candidate": {}, "confidence": 0.0, "item_id": None}

    # 2. LLM 提炼
    candidate = await refine_experience(
        client, agent_key, session_id, user_message, final_text, tool_events,
        prior_history, scope,
    )
    if not candidate.get("title") or not candidate.get("content"):
        return {"auto_saved": False, "candidate": candidate, "confidence": 0.0, "item_id": None}

    # 3. 置信度评分
    confidence = await _score_confidence(client, candidate)

    # 4. 高置信度自动入库
    if confidence >= _AUTO_SAVE_CONFIDENCE:
        item = candidate_to_item(candidate, agent_key, session_id)
        if store:
            try:
                store.create(item.to_dict())
                logger.info(
                    "经验自动入库: %s (置信度 %.2f, agent=%s, session=%s)",
                    item.title, confidence, agent_key, session_id,
                )
                return {
                    "auto_saved": True,
                    "candidate": candidate,
                    "confidence": confidence,
                    "item_id": item.id,
                }
            except Exception as exc:
                logger.warning("经验自动入库失败: %s", exc)

    return {
        "auto_saved": False,
        "candidate": candidate,
        "confidence": confidence,
        "item_id": None,
    }
