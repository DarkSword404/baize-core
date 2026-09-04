"""Learning Pipeline：Episode →（确定性实体）→ 经验提炼 → 证据链接 → 落库。

流程（Hermes closed learning loop 的“经验版”，不含任何 Skill 生成/执行）：
1. 从 Episode 全文本确定性抽取实体并写入知识图；
2. 规则信号判定“本次值得沉淀吗”（有结论 / 绕路后成功 / 深度工具链…）；
3. 值得则用 LLM（或规则回退）把 Episode 复盘成一条自然语言经验；
4. 计算置信度：≥0.7 直接 active；否则 draft（等待用户 REVISE/确认），
   二者都会落库——低置信度经验也是候选记忆，不丢弃；
5. 经验自带证据链（EvidenceRef → Episode 内 step），可回溯原始执行轨迹。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .episode import EpisodeBuilder
from .graph import KnowledgeGraph
from .models import EpisodeRecord, attach_evidence
from .store import MemoryStore
from .utils import excerpt, new_id, now_iso, token_dict

logger = logging.getLogger("baize.memory.learn")

AUTO_ACTIVE_CONFIDENCE = 0.70

# 结论信号（命中即任务有明确产出）
CONCLUSION_KEYWORDS = [
    "成功", "拿下", "shell", "flag", "漏洞", "验证", "确认", "发现", "利用",
    "成功登录", "绕过", "提权", "getshell", "pwned", "found", "success",
    "exploited", "verified", "弱口令", "上传成功",
]
# 失败信号
FAILURE_KEYWORDS = [
    "失败", "错误", "超时", "无法", "不能", "不通", "无结果",
    "failed", "error", "timeout", "unable", "refused", "denied",
]
# 沙箱审批属正常流程，不应判为失败
SANDBOX_PATTERNS = ["需要审批", "审批", "sandbox", "approval", "denied by user",
                    "沙箱策略拦截"]
# 用户纠正/引导措辞
GUIDANCE_KEYWORDS = ["应该", "试试", "换", "不要", "别用", "记得", "先", "用这个"]


def _clean(text: str) -> str:
    low = text
    for p in SANDBOX_PATTERNS:
        low = low.replace(p, "")
    return low


def _has_any(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


class LearnSignals:
    """纯规则判定是否值得提炼，结果可审计。"""

    def __init__(self, episode: dict):
        self.episode = episode
        steps = episode.get("steps", [])
        tool_text = _clean(" ".join(
            f"{s.get('tool_name','')} {s.get('arguments','')} {s.get('output','')}"
            for s in steps if s.get("type") == "tool_call"))
        result = _clean((episode.get("result_text") or "") + " " +
                        (episode.get("summary") or ""))
        error = _clean(episode.get("error_text") or "")
        self.concluded = _has_any(result, CONCLUSION_KEYWORDS)
        self.failed_once = _has_any(tool_text + " " + error, FAILURE_KEYWORDS)
        self.error_then_success = bool(self.failed_once and self.concluded)
        self.tool_calls = sum(1 for s in steps if s.get("type") == "tool_call")
        self.has_output = bool((episode.get("result_text") or "").strip())
        self.has_error = bool((episode.get("error_text") or "").strip())

    def meaningful(self) -> bool:
        """没有任何产出/过程也没有错误 → 不值得沉淀（空转/闲聊）。"""
        if self.has_output or self.has_error:
            return True
        if self.tool_calls:
            return True
        if self.episode.get("task"):
            return True
        return False

    def confidence(self) -> float:
        score = 0.30
        if self.concluded:
            score += 0.30
        if self.error_then_success:
            score += 0.20
        elif self.failed_once:
            score += 0.10
        if self.tool_calls:
            score += 0.10
        if self.tool_calls >= 5:
            score += 0.05
        return round(max(0.0, min(1.0, score)), 2)

    def reasons(self) -> list[str]:
        r = []
        if self.concluded:
            r.append("任务有明确结论")
        if self.error_then_success:
            r.append("绕路后走通（错误→成功）")
        elif self.failed_once:
            r.append("本次出现失败，失败原因有记录价值")
        if self.tool_calls:
            r.append(f"有 {self.tool_calls} 次工具调用轨迹")
        return r


def _build_evidence(episode: dict) -> list[dict]:
    """从 Episode 中挑出最有信息量的片段作为证据快照。"""
    eid = episode.get("id", "")
    evidence: list[dict] = []
    task = (episode.get("task") or "").strip()
    if task:
        evidence.append(attach_evidence(eid, kind="task", ref="task", excerpt_text=task[:240]))
    # 工具调用：保留最近有产出的 6 次
    tool_steps = [s for s in episode.get("steps", [])
                  if s.get("type") == "tool_call" and s.get("output")]
    for s in tool_steps[-6:]:
        head = f"[{s.get('tool_name','')}] {excerpt(s.get('output',''), 200)}"
        evidence.append(attach_evidence(
            eid, kind="tool", ref=s.get("id", ""), excerpt_text=head))
    if episode.get("result_text"):
        evidence.append(attach_evidence(
            eid, kind="result", ref="result",
            excerpt_text=excerpt(episode.get("result_text", ""), 300)))
    if episode.get("error_text"):
        evidence.append(attach_evidence(
            eid, kind="result", ref="error",
            excerpt_text=excerpt(episode.get("error_text", ""), 300)))
    return evidence


def _material(episode: dict) -> str:
    lines = [f"任务：{episode.get('task','')}",
             f"目标：{episode.get('target','')}"]
    for s in episode.get("steps", [])[:40]:
        if s.get("type") == "tool_call":
            lines.append(f"- 工具 {s.get('tool_name','')}: "
                         f"参数 {excerpt(s.get('arguments',''),200)} "
                         f"=> 输出 {excerpt(s.get('output',''),260)}")
        elif s.get("text"):
            lines.append(f"- [{s.get('type')}] {excerpt(s.get('text',''),240)}")
    if episode.get("result_text"):
        lines.append(f"结论：{episode.get('result_text','')[:1200]}")
    if episode.get("error_text"):
        lines.append(f"错误：{episode.get('error_text','')[:600]}")
    return "\n".join(lines)[:8000]


class MemoryLearner:
    def __init__(self, store: MemoryStore, graph: KnowledgeGraph,
                 client=None):
        self.store = store
        self.graph = graph
        self.client = client  # LLM client（可空，空则走规则提炼）

    # ---- 公开入口 ---------------------------------------------------------
    async def learn(self, episode: EpisodeRecord,
                    user_message: str = "") -> dict:
        ep = self.store.save_episode(episode.to_dict())
        signals = LearnSignals(ep)

        # 1) 实体抽离并写入知识图
        texts = [ep.get("task", ""), ep.get("target", ""),
                 ep.get("result_text", ""), ep.get("error_text", "")]
        for s in ep.get("steps", []):
            texts.append(s.get("tool_name", ""))
            texts.append(s.get("arguments", ""))
            if s.get("type") == "tool_call":
                texts.append(s.get("output", ""))
            else:
                texts.append(s.get("text", ""))
        entity_keys = self.graph.record_entities(ep["id"], texts)
        if entity_keys:
            self.graph.link_mentions("episode", ep["id"], entity_keys,
                                     provenance_episode=ep["id"])
            ep["entity_keys"] = entity_keys
            self.store.save_episode(ep)

        # 2) 信号判定：无意义的会话只留 Episode，不强行提炼经验
        if not signals.meaningful():
            return {"episode_id": ep["id"], "experience_created": False,
                    "reason": "no-meaningful-signal",
                    "confidence": signals.confidence(),
                    "entity_keys": entity_keys}

        # 3) 经验候选（LLM 优先，失败/无 LLM 用规则回退）
        candidate = await self._refine(ep)
        if not candidate:
            return {"episode_id": ep["id"], "experience_created": False,
                    "reason": "refine-empty", "confidence": signals.confidence(),
                    "entity_keys": entity_keys}

        confidence = signals.confidence()
        candidate["confidence"] = confidence
        candidate["status"] = ("active" if confidence >= AUTO_ACTIVE_CONFIDENCE
                               else "draft")

        # 4) 落库：经验本体 + 证据链 + 与 Episode/实体的图谱关系
        exp = self._to_record(ep, candidate, confidence)
        self.store.save_experience(exp)

        self.graph.record_relation(
            "SUPPORTS", f"episode:{ep['id']}", f"experience:{exp['id']}",
            provenance_episode=ep["id"])
        if exp.get("entity_keys"):
            self.graph.link_mentions("experience", exp["id"],
                                     exp["entity_keys"],
                                     provenance_episode=ep["id"])

        return {
            "episode_id": ep["id"],
            "experience_created": True,
            "experience_id": exp["id"],
            "title": exp.get("title", ""),
            "status": exp.get("status"),
            "confidence": confidence,
            "reasons": signals.reasons(),
            "entity_keys": entity_keys,
        }

    # ---- 内部：提炼 ----------------------------------------------------------
    async def _refine(self, episode: dict) -> Optional[dict]:
        material = _material(episode)
        if self.client is not None:
            try:
                result = await self._refine_with_llm(material)
                if result:
                    return result
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM 经验提炼失败，回退规则提炼: %s", exc)
        return self._refine_rules(episode, material)

    async def _refine_with_llm(self, material: str) -> Optional[dict]:
        from baize.sdk.client import ChatMessage

        system = (
            "你是一名资深渗透测试专家，负责把一次渗透测试过程复盘为可复用经验。\n"
            "要求：\n"
            "1) title：一句话概括（如 '对某 Tomcat 弱口令后台的打点套路'）；\n"
            "2) content：自然语言复盘，200 字以内，包含『踩坑点/教训』与『可复用步骤或技巧』"
            "以及『适用条件（目标指纹/版本特征）』；\n"
            "3) tags：3-6 个检索标签（技术关键词）；\n"
            "4) kind：method(可复用方法)/lesson(教训避坑)/intel(情报事实) 三选一。\n"
            "只输出 JSON："
            "{\"title\":\"...\",\"content\":\"...\",\"tags\":[...],\"kind\":\"method\"}"
        )
        result = await self.client.complete([
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=material),
        ])
        text = (result.content or "") if result else ""
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
        title = str(parsed.get("title", "")).strip()
        content = str(parsed.get("content", "")).strip()
        if not title or not content:
            return None
        kind = str(parsed.get("kind", "method"))
        if kind not in ("method", "lesson", "intel"):
            kind = "method"
        return {"title": title, "content": content,
                "tags": [str(t).strip() for t in parsed.get("tags", [])
                         if str(t).strip()][:8], "kind": kind}

    def _refine_rules(self, episode: dict, material: str) -> dict:
        """无 LLM 回退：把 Episode 的事实转录成结构化自然语言经验。"""
        task = (episode.get("task") or "渗透测试过程").strip()[:60]
        tools = [s.get("tool_name") for s in episode.get("steps", [])
                 if s.get("type") == "tool_call" and s.get("tool_name")]
        unique_tools = list(dict.fromkeys(tools))
        result = (episode.get("result_text") or "").strip()
        error = (episode.get("error_text") or "").strip()

        parts = []
        if error:
            parts.append(f"踩坑：{excerpt(error, 400)}")
        lines = []
        for s in episode.get("steps", [])[:24]:
            if s.get("type") == "tool_call":
                out = excerpt(s.get("output", ""), 200)
                lines.append(f"{s.get('tool_name','')}: {out}")
        if lines:
            parts.append("执行要点：" + "；".join(lines)[:600])
        if result:
            parts.append(f"结果：{excerpt(result, 300)}")
        content = "；".join(parts)[:900] or "（过程无文本产出，请手动补充）"
        tags = [t for t in unique_tools if t][:4] or ["渗透测试"]
        return {
            "title": f"{task}（过程复盘）",
            "content": content,
            "tags": tags,
            "kind": "lesson" if error else "method",
        }

    def _to_record(self, episode: dict, candidate: dict,
                   confidence: float) -> dict:
        entity_keys = list(episode.get("entity_keys", []))
        record = {
            "id": new_id("exp"),
            "title": candidate.get("title", ""),
            "content": candidate.get("content", ""),
            "tags": candidate.get("tags", []) or [],
            "kind": candidate.get("kind", "method"),
            "status": candidate.get("status", "draft"),
            "confidence": confidence,
            "scope": "global",
            "agent_key": episode.get("agent_key", ""),
            "source_session_id": episode.get("session_id", ""),
            "episode_id": episode.get("id", ""),
            "evidence": _build_evidence(episode),
            "entity_keys": entity_keys,
            "supersedes": [],
            "replaced_by": "",
            "history": [{"action": "learn", "at": now_iso(),
                         "note": f"置信度 {confidence:.2f}"}],
            "importance": int(round(confidence * 5)),
            # 评价闭环计数：命中=被注入上下文；有用/无用=是否被采纳
            "hit_count": 0,
            "useful_count": 0,
            "noise_count": 0,
            "last_hit_at": "",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "source": "agent",
        }
        return record
