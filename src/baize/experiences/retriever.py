"""经验检索器。

检索策略（可插拔）：
- 未配置向量 Provider：纯关键词 + 标签 + 重要性加权（零依赖）。
- 已配置向量 Provider：向量余弦 + 关键词 混合打分（embedding 缺失的条目自动回退关键词分）。
- hit_count 参与排序，帮助沉淀真正被复用过的经验。

注入格式：将 top-N 条经验渲染为 system 提示中的"历史经验"块。
"""

from __future__ import annotations

import re
from typing import Optional

from .embedding import BaseEmbedding, EmbeddingConfigStore, resolve_embedding
from .store import AGENT_PREFIX, GLOBAL_SCOPE, ExperienceItem, ExperienceStore

STOPWORDS = {"的", "了", "和", "是", "在", "我", "你", "他", "它", "有", "与", "及",
             "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are",
             "this", "that", "with", "as", "at", "by", "from", "be", "was", "were",
             "do", "does", "did", "not", "it", "its", "we", "they", "them", "you", "your"}


def _tokenize(text: str) -> set[str]:
    """粗粒度分词：提取 CJK 二元组与英文单词，用于关键词匹配。"""
    text = text.lower()
    tokens: set[str] = set()
    for m in re.finditer(r"[a-z0-9_\-\./]+", text):
        w = m.group(0)
        if len(w) > 1 and w not in STOPWORDS:
            tokens.add(w)
    # 中文：相邻字符二元组
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", text)
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i : i + 2])
    return tokens


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class ExperienceRetriever:
    def __init__(
        self,
        store: ExperienceStore,
        embedding: Optional[BaseEmbedding] = None,
    ) -> None:
        self._store = store
        self._embedding = embedding or resolve_embedding()
        # query 向量按查询文本缓存（避免同一检索器实例跨查询串用旧向量）
        self._query_vec_cache: Optional[list[float]] = None
        self._query_vec_cache_text: str = ""

    def embedding_ready(self) -> bool:
        return self._embedding.is_available()

    def embedding_model(self) -> str:
        return self._embedding.model_name

    def _keyword_score(self, q_tokens: set[str], item: ExperienceItem) -> float:
        if not q_tokens:
            return 0.0
        hay = _tokenize(f"{item.title} {item.content} {' '.join(item.tags)}")
        hits = q_tokens & hay
        if not hits:
            return 0.0
        # 标题与标签命中权重更高
        title_tokens = _tokenize(item.title + " " + " ".join(item.tags))
        title_hits = q_tokens & title_tokens
        return (len(title_hits) * 1.5 + len(hits)) / (2.0 * len(q_tokens) + 0.001)

    async def _vector_score(self, q_tokens: set[str], query: str, item: ExperienceItem) -> float:
        """计算 query 与条目的向量相似度；无向量则回退关键词分。"""
        if not self._embedding.is_available():
            return self._keyword_score(q_tokens, item)
        if item.embedding and item.embedding_model == self._embedding.model_name:
            # 同一查询只计算一次；查询变化时重新计算，避免串用旧向量
            if self._query_vec_cache is None or self._query_vec_cache_text != query:
                try:
                    vecs = await self._embedding.embed([query])
                except Exception:  # noqa: BLE001
                    vecs = []
                self._query_vec_cache = vecs[0] if vecs else None
                self._query_vec_cache_text = query
            vec = self._query_vec_cache
            if vec and len(vec) == len(item.embedding):
                return _cosine(vec, item.embedding)
        return self._keyword_score(q_tokens, item)

    async def search(
        self,
        query: str,
        agent_key: str,
        top_k: int = 3,
        min_score: float = 0.5,
    ) -> list[ExperienceItem]:
        """检索与查询相关的经验条目（全局 + 当前智能体专属，优先智能体经验）。"""
        scopes = [GLOBAL_SCOPE, f"{AGENT_PREFIX}{agent_key}"]
        candidates = self._store.list_items(include_disabled=False)
        candidates = [i for i in candidates if i.scope in scopes]

        q_tokens = _tokenize(query)
        if not q_tokens and not self._embedding.is_available():
            return []

        scored: list[tuple[float, ExperienceItem]] = []
        for item in candidates:
            kw = self._keyword_score(q_tokens, item)
            vec = await self._vector_score(q_tokens, query, item)
            # 混合：向量分占大头（语义更准），关键词分补充；智能体专属经验加权
            score = max(kw, vec) if (self._embedding.is_available() and vec > 0) else kw
            if item.scope != GLOBAL_SCOPE:
                score *= 1.1  # 智能体专属经验优先
            score += min(item.importance * 0.05, 0.25)  # 重要条目加权
            score += min(item.hit_count * 0.01, 0.1)  # 被复用过的经验加权
            if score >= min_score:
                scored.append((score, item))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def build_block(self, items: list[ExperienceItem]) -> str:
        """将检索到的经验渲染为注入到 system 提示的文本块。"""
        if not items:
            return ""
        lines = [
            "【历史经验 · 仅供参考，需结合实际目标验证后使用】",
            "以下是与本次任务相关的历史经验总结，来自以往渗透测试复盘：",
            "",
        ]
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item.title}")
            if item.tags:
                lines.append(f"   标签: {', '.join(item.tags)}")
            lines.append(f"   内容: {item.content.strip()}")
            lines.append("")
        return "\n".join(lines).strip()
