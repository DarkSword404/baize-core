"""长期记忆：经验库模块。

核心能力：
- ExperienceStore：经验条目持久化（JSON，按作用域分文件）与 CRUD
- Embedding Provider：可插拔向量方案（none / openai 云端 / local 本地）
- ExperienceRetriever：关键词 + 可选向量混合检索，注入 agent 上下文
- refine：LLM 复盘提炼 + 信号检测（自动提炼 + 用户确认）
"""

from .embedding import (
    BaseEmbedding,
    EmbeddingConfig,
    EmbeddingConfigStore,
    LocalEmbedding,
    NoneEmbedding,
    OpenAIEmbedding,
    resolve_embedding,
)
from .refine import (
    build_context_material,
    candidate_to_item,
    detect_turn_signals,
    refine_experience,
)
from .retriever import ExperienceRetriever
from .store import (
    AGENT_PREFIX,
    GLOBAL_SCOPE,
    ExperienceItem,
    ExperienceStore,
    new_id,
    now_iso,
)

__all__ = [
    "BaseEmbedding",
    "EmbeddingConfig",
    "EmbeddingConfigStore",
    "LocalEmbedding",
    "NoneEmbedding",
    "OpenAIEmbedding",
    "resolve_embedding",
    "ExperienceRetriever",
    "ExperienceStore",
    "ExperienceItem",
    "GLOBAL_SCOPE",
    "AGENT_PREFIX",
    "new_id",
    "now_iso",
    "detect_turn_signals",
    "build_context_material",
    "refine_experience",
    "candidate_to_item",
]
