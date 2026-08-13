"""可插拔的向量嵌入 Provider。

支持四种模式（配置存于 ~/.baize/embedding.json）：
- provider = "none"       默认：不做向量化，检索器退化为纯关键词+标签匹配（零依赖）
- provider = "openai"     云端：任意 OpenAI 兼容 embedding 端点（OpenAI / 硅基流动 / 智谱等）
- provider = "dashscope"  云端：DashScope 多模态向量（qwen3-vl-embedding，走百炼原生多模态接口）
- provider = "local"      本地：fastembed（ONNX 量化，轻量 CPU 推理）

未配置云端时自动回退复用 LLM 配置的 base_url/api_key（若该端点支持 embedding）。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

BAIZE_DIR = Path.home() / ".baize"
EMBEDDING_CONFIG_PATH = BAIZE_DIR / "embedding.json"


@dataclass
class EmbeddingConfig:
    provider: str = "none"  # none | openai | local
    base_url: str = ""
    api_key: str = ""
    model: str = ""  # 例如 text-embedding-3-small / BAAI/bge-small-zh-v1.5
    dimensions: int = 0  # 0 表示自动探测


class EmbeddingConfigStore:
    """~/.baize/embedding.json 的读写（与 ModelConfigStore 风格一致）。"""

    def load(self) -> EmbeddingConfig:
        if EMBEDDING_CONFIG_PATH.exists():
            try:
                raw = json.loads(EMBEDDING_CONFIG_PATH.read_text(encoding="utf-8"))
                return EmbeddingConfig(**{k: raw[k] for k in EmbeddingConfig.__dataclass_fields__ if k in raw})
            except (json.JSONDecodeError, OSError, TypeError):
                pass
        return EmbeddingConfig()

    def save(self, cfg: EmbeddingConfig) -> None:
        BAIZE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = EMBEDDING_CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, EMBEDDING_CONFIG_PATH)


class BaseEmbedding:
    """向量 Provider 抽象接口。"""

    provider = "none"

    @property
    def model_name(self) -> str:
        return ""

    def is_available(self) -> bool:
        return False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class NoneEmbedding(BaseEmbedding):
    provider = "none"


class OpenAIEmbedding(BaseEmbedding):
    """OpenAI 兼容云端 embedding（复用 openai SDK）。"""

    provider = "openai"

    def __init__(self, cfg: EmbeddingConfig, fallback_base_url: str = "", fallback_api_key: str = "") -> None:
        self._cfg = cfg
        self._base_url = cfg.base_url or fallback_base_url
        self._api_key = cfg.api_key or fallback_api_key
        self._model = cfg.model
        self._client = None
        self._dim = cfg.dimensions

    @property
    def model_name(self) -> str:
        return self._model or f"{self._base_url}"

    def is_available(self) -> bool:
        return bool(self._base_url and self._model)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=self._base_url,
                api_key=self._api_key or "sk-placeholder",
            )
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        vectors = [d.embedding for d in resp.data]
        if self._dim == 0 and vectors:
            self._dim = len(vectors[0])
        return vectors


class LocalEmbedding(BaseEmbedding):
    """本地 ONNX 量化 embedding（fastembed，轻量 CPU 推理）。

    依赖：pip install fastembed（约几百 MB，含 onnxruntime）。
    首用会自动下载模型；可用 sentence-transformers 作后备。
    """

    provider = "local"

    def __init__(self, cfg: EmbeddingConfig) -> None:
        self._cfg = cfg
        self._model = cfg.model or "BAAI/bge-small-zh-v1.5"
        self._engine = None
        self._engine_type = ""

    @property
    def model_name(self) -> str:
        return self._model

    def is_available(self) -> bool:
        return True  # 实际可用性在首次 embed 时验证（依赖可能未安装）

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._engine is None:
            self._engine, self._engine_type = await asyncio.to_thread(self._load_engine)
        if self._engine_type == "fastembed":
            result = await asyncio.to_thread(self._engine.embed, texts)
            return [list(v) for v in result]
        # sentence-transformers 后备
        result = await asyncio.to_thread(self._engine.encode, texts, normalize_embeddings=True)
        return [v.tolist() for v in result]

    def _load_engine(self):
        try:
            from fastembed import TextEmbedding

            return TextEmbedding(model_name=self._model), "fastembed"
        except Exception:  # noqa: BLE001
            from sentence_transformers import SentenceTransformer

            return SentenceTransformer(self._model), "st"


def resolve_embedding(cfg: Optional[EmbeddingConfig] = None) -> BaseEmbedding:
    """根据配置构建可用的 Embedding Provider。

    未显式配置云端时，自动复用 LLM 模型配置的 base_url/api_key，
    便于用户在设置页只填一次即可同时获得对话 + 向量能力。
    """
    cfg = cfg or EmbeddingConfigStore().load()

    if cfg.provider == "local":
        return LocalEmbedding(cfg)

    if cfg.provider == "openai":
        if cfg.base_url and cfg.model:
            return OpenAIEmbedding(cfg)
        # 云端未填全：尝试复用 LLM 配置
        from baize.sdk.client import get_active_model_config

        llm = get_active_model_config()
        if llm is not None and llm.base_url:
            merged = EmbeddingConfig(
                provider="openai",
                base_url=cfg.base_url or llm.base_url,
                api_key=cfg.api_key or llm.api_key or "",
                model=cfg.model,
                dimensions=cfg.dimensions,
            )
            return OpenAIEmbedding(merged)
        return NoneEmbedding()

    return NoneEmbedding()
