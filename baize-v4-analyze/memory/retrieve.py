"""Hybrid Retrieval：经验文本 + 语义事实 + 实体邻域图扩展的混合召回。

不调用 LLM，纯关键词/实体匹配，保证可复现、零成本。返回结构让 Agent / API / UI
都能拿到按类型分组的记忆，并由上层决定注入格式。混合方式：
1. 经验层：对 experience 的 title/content/tags 做词权重打分（BM25 变体）；
2. 知识层：查询文本确定性抽取实体 + 对实体库标签/别名做子串命中；
3. 图扩展：从命中实体沿「当前有效」边扩展 1 跳邻域（Semantic 邻域召回）；
4. 事实层：与命中实体 / 关键词相关的事实文本召回。
"""
from __future__ import annotations

import logging
from typing import Optional

from .graph import KnowledgeGraph, extract_entities_from_text
from .store import MemoryStore
from .utils import excerpt, token_dict

logger = logging.getLogger("baize.memory.retrieve")

_STOP = {"一个", "一些", "这个", "那个", "进行", "使用", "以及", "并且",
         "如何", "什么", "怎么", "我们", "可以", "需要", "已经", "没有",
         "the", "a", "an", "and", "for", "with", "how", "what"}


def _terms(text: str) -> dict[str, int]:
    return {t: c for t, c in token_dict(text).items() if t not in _STOP}


def _overlap_score(a: dict[str, int], b: dict[str, int]) -> float:
    """轻量文本相似：查询词在目标中出现的加权占比，稀有词有增益。"""
    if not a or not b:
        return 0.0
    score = 0.0
    hit = 0
    for t, c in a.items():
        if t in b:
            score += c + b[t]
            hit += 1
    if not hit:
        return 0.0
    return score / (sum(a.values()) + len(a) * 0.3)


class MemoryRetriever:
    """混合召回器。

    search() 返回:
      experiences —— 自然语言经验（正文/标题/标签打分）
      facts       —— 与命中实体 / 关键词相关的语义事实
      entities    —— 查询命中的实体
      neighbors   —— 从命中实体沿有效边扩展邻域 {entities, relations}
      episodes    —— 任务级召回（仅 include_episodes=True 时参与）
    """

    def __init__(self, store: MemoryStore, graph: Optional[KnowledgeGraph] = None):
        self.store = store
        self.graph = graph or KnowledgeGraph(store)

    # ---- 主入口 -------------------------------------------------------------
    def search(self, query: str, k_experiences: int = 5, k_facts: int = 5,
               k_episodes: int = 3, include_episodes: bool = False) -> dict:
        q_terms = _terms(query)
        if not q_terms:
            return {"query": query, "experiences": [], "facts": [],
                    "entities": [], "neighbors": {"entities": [], "relations": []},
                    "episodes": []}
        entities = self._match_entities(query)
        related = self._expand(entities)
        entity_keys = [e["key"] for e in entities] + [e["key"] for e in related["entities"]]
        experiences = self._rank_experiences(q_terms, k_experiences)
        facts = self._rank_facts(q_terms, entity_keys, k_facts)
        episodes = (self._rank_episodes(q_terms, k_episodes)
                    if include_episodes else [])
        return {
            "query": query,
            "experiences": experiences,
            "facts": facts,
            "entities": entities,
            "neighbors": related,
            "episodes": episodes,
        }

    # ---- 实体命中 ------------------------------------------------------------
    def _match_entities(self, query: str) -> list[dict]:
        low = query.lower()
        matched: dict[str, dict] = {}
        for key, ent in extract_entities_from_text(query).items():
            matched[key] = ent
        for ent in self.store.list_entities(limit=3000):
            if ent["key"] in matched:
                continue
            hay = [ent.get("label", ""), ent.get("key", "")] + list(ent.get("aliases", []))
            if any(h and len(h) >= 2 and h.lower() in low for h in hay):
                matched[ent["key"]] = ent
        return list(matched.values())

    # ---- 图邻域扩展 ------------------------------------------------------------
    def _expand(self, entities: list[dict]) -> dict:
        keys = [e["key"] for e in entities]
        if not keys:
            return {"entities": [], "relations": []}
        return self.graph.expand(keys, hop=1)

    # ---- 经验打分 ---------------------------------------------------------------
    def _rank_experiences(self, q_terms: dict[str, int], k: int) -> list[dict]:
        scored: list[tuple[float, dict]] = []
        for exp in self.store.list_experiences():
            if exp.get("status") != "active":
                continue
            title = _terms(exp.get("title", ""))
            content = _terms(exp.get("content", ""))
            tags = _terms(" ".join(exp.get("tags", [])))
            score = _overlap_score(q_terms, content) * 0.7 \
                + _overlap_score(q_terms, title) * 0.8 \
                + _overlap_score(q_terms, tags) * 1.0
            # 排序加权：重要度 + 历史评价（被采纳越多越靠前，被判无用则降权）
            useful_n = min(exp.get("useful_count", 0), 20)
            hit_n = min(exp.get("hit_count", 0), 50)
            noise_n = min(exp.get("noise_count", 0), 10)
            weight = 1.0 + min(exp.get("importance", 0), 10) * 0.02 \
                + useful_n * 0.03 + hit_n * 0.005 - noise_n * 0.02
            score *= max(0.5, weight)
            if score > 0:
                scored.append((score, exp))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for _, exp in scored[:k]:
            item = dict(exp)
            item["content"] = excerpt(item.get("content", ""), 600)
            item["evidence"] = item.get("evidence", [])[:3]
            out.append(item)
        return out

    # ---- 事实打分 ---------------------------------------------------------------
    def _rank_facts(self, q_terms: dict[str, int], entity_keys: list[str],
                    k: int) -> list[dict]:
        scored: list[tuple[float, dict]] = []
        keyset = set(entity_keys)
        for fact in self.store.list_facts():
            if fact.get("status") != "active":
                continue
            score = 0.0
            if fact.get("subject") in keyset or fact.get("object") in keyset:
                score = 0.5
            text_terms = _terms(fact.get("statement", ""))
            s = _overlap_score(q_terms, text_terms)
            score = max(score, s * 1.2)
            if score > 0:
                scored.append((score, fact))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [dict(f) for _, f in scored[:k]]

    # ---- Episode 召回（回忆用） -----------------------------------------------------
    def _rank_episodes(self, q_terms: dict[str, int], k: int) -> list[dict]:
        scored: list[tuple[float, dict]] = []
        for ep in self.store.list_episodes(limit=200):
            if ep.get("status") == "discarded":
                continue
            text = _terms((ep.get("task", "") + " " + ep.get("summary", "") +
                           " " + ep.get("result_text", "")))
            score = _overlap_score(q_terms, text)
            if score > 0:
                ep = dict(ep)
                ep["steps"] = ep.get("steps", [])[:6]
                scored.append((score, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:k]]

    # ---- 注入格式 ---------------------------------------------------------------
    def build_injection(self, query: str, budget_chars: int = 3200,
                        include_episodes: bool = False,
                        data: Optional[dict] = None) -> str:
        """把混合检索结果组装成注入 Agent 上下文的段落（保守、不喧宾夺主）。

        经验始终以自然语言文本呈现，Agent 自行判断是否参考。
        data 可传入已完成的 search 结果，避免同一轮重复检索。
        """
        if data is None:
            data = self.search(query, k_experiences=5, k_facts=4,
                               include_episodes=include_episodes)
        parts: list[str] = []
        budget = budget_chars
        header = ("以下是长期记忆库中可能与当前任务相关的既往知识与经验"
                  "（仅作参考，请结合实时工具结果判断其是否适用）：")
        body: list[str] = []

        exps = data["experiences"]
        if exps:
            lines: list[str] = []
            for e in exps:
                lines.append(f"[经验] {e.get('title','')}｜{e.get('kind','')}")
                lines.append(f"      {e.get('content','')}")
            body.append("\n".join(lines)[:int(budget * 0.6)])
            budget -= int(budget * 0.6)

        facts = data["facts"]
        if facts and budget > 400:
            lines = [f"[事实] {f.get('statement','')}（{f.get('confidence','')}）"
                     for f in facts]
            body.append("\n".join(lines)[:int(budget * 0.6)])
            budget -= int(budget * 0.6)

        ents = data["entities"] + data["neighbors"]["entities"]
        if ents and budget > 200:
            uniq = list({e["key"]: e for e in ents}.values())[:10]
            labels = [f"{e.get('label','')}({e.get('kind','')})" for e in uniq]
            body.append("知识库中已记录的关联实体：" + "、".join(labels))

        if body:
            parts.append(header)
            parts.extend(body)
        return "\n".join(parts)
