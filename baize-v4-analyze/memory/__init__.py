"""Baize 长期知识与经验记忆系统（全新实现，独立于历史经验模块）。

架构（Temporal Knowledge Graph + Text-based Experience Memory + Hybrid
Retrieval，参考 Zep/Graphiti 与 Hermes）：

    Working    —— 由 Agent 上下文本体承载（本包不复制）
    Episode    —— 每次任务一份原始轨迹快照（.episode、.store）
    Knowledge  —— 实体 + 带时间有效窗的关系边（.graph）
    Semantic   —— 结构化语义事实（FactRecord，.store 的 facts）
    Experience —— 自然语言经验（ExperienceRecord，.store 的 experiences）
    Evidence   —— 经验↔Episode 片段的证据链接（EvidenceRef）
    Evolution  —— REVISE/SUPERSEDE/INVALIDATE + 合并概括（.evolve）
    Retrieval  —— 混合召回与上下文注入（.retrieve）
    Facade     —— MemoryService（.service）

不生成/不执行 Skill；经验以自然语言为存储核心，由 Agent 检索后自行判断。
"""
from .episode import EpisodeBuilder, EpisodeRecord, build_from_log
from .evolve import consolidate, revise, set_status, supersede
from .graph import KnowledgeGraph, extract_entities_from_text
from .learn import MemoryLearner
from .models import (CONTENT_STATUSES, EPISODE_STATUSES, EvidenceRef,
                     ExperienceRecord, FactRecord)
from .retrieve import MemoryRetriever
from .service import MemoryService
from .store import MemoryStore

__all__ = [
    "MemoryService", "MemoryStore", "MemoryLearner", "MemoryRetriever",
    "KnowledgeGraph", "EpisodeBuilder", "EpisodeRecord", "ExperienceRecord",
    "FactRecord", "EvidenceRef", "extract_entities_from_text",
    "build_from_log", "consolidate", "revise", "set_status", "supersede",
    "CONTENT_STATUSES", "EPISODE_STATUSES",
]
