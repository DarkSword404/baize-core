"""可选的语义检索层（embedding）。

BM25 只做字面匹配：查「RCE」召回不到「远程代码执行」——除非实体库里有人手工
加过别名。本模块补上语义通道，但严格保持**可选**：

1. 未配置服务时 enabled() 为 False，检索完全走 BM25，行为与接入前一致。
   embedding 走外部 HTTP 服务（OpenAI 兼容的 /v1/embeddings），项目本身依然
   是零 ML 依赖——这是刻意的选择，渗透场景常在内网离线跑，不该把向量模型
   塞进运行时。服务地址与密钥默认复用已配置的 LLM 服务（通常是同一供应商、
   同一个 key），可用 BAIZE_EMBEDDING_BASE_URL / _API_KEY 覆盖；模型不跟随
   LLM——聊天模型与向量模型是两回事，默认 text-embedding-v4（选型理由见
   DEFAULT_EMBEDDING_MODEL 处），可用配置文件的 embedding_model 指定，
   环境变量 BAIZE_EMBEDDING_MODEL 优先于它。
2. 检索路径是同步的（Agent 与 API 都在同步上下文调 recall/search），没法
   await，所以文档向量必须**预计算落盘**，检索时只做纯 Python 余弦。
3. 查询向量只能现算，放在后台线程的事件循环里同步等待，不阻塞主事件循环；
   失败即降级并熔断一段时间，避免服务不可用时每次检索都卡在超时上。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("baize.memory.embed")

DEFAULT_TIMEOUT = 8.0
CIRCUIT_SECONDS = 300.0
_CACHE_MAX = 512
# 不用 qwen3-vl-embedding：它是多模态（图/视频/文本）embedding，官方明确不支持
# OpenAI 兼容模式——实测返回 404 并提示 "Unsupported model ... for OpenAI
# compatibility mode"，而本模块走的正是 /v1/embeddings。同一个 key、同一个
# base_url 下 text-embedding-v4 可用（1024 维），故以此为默认。
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _llm_model_config():
    """取已配置的 LLM 服务（base_url / api_key），供 embedding 复用。

    拿不到就返回 None——配置缺失或文件损坏都只该让语义通道缺席，不该让检索
    抛错，所以这里吞掉一切异常。
    """
    try:
        from ..config import ModelConfigStore

        return ModelConfigStore().load()
    except Exception:  # noqa: BLE001
        return None


def fingerprint(text: str) -> str:
    """向量对应的文本指纹。内容改了向量就过期，检索时据此跳过而不是拿错向量。"""
    return hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()[:16]


def experience_text(exp: dict) -> str:
    """经验参与向量化的文本。

    写入侧与检索侧必须用同一段文本，指纹才对得上，因此收敛到这里。
    """
    return "\n".join([
        exp.get("title", "") or "",
        exp.get("content", "") or "",
        " ".join(exp.get("tags") or []),
    ]).strip()


def _endpoint(base: str) -> str:
    """把用户给的 base_url 归一到 /v1/embeddings。"""
    b = (base or "").rstrip("/")
    for suffix in ("/embeddings", "/chat/completions"):
        if b.endswith(suffix):
            b = b[: -len(suffix)]
    if not b.endswith("/v1"):
        b += "/v1"
    return b + "/embeddings"


def cosine(a, b) -> float:
    """纯 Python 余弦相似度。

    不引入 numpy：参与精排的候选只有几十条，纯 Python 足够快；而向量检索是
    可选功能，不该为它把数值计算库变成硬依赖。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


class Embedder:
    """OpenAI 兼容 /v1/embeddings 的客户端；未配置即整体降级。"""

    def __init__(self, base: str = "", model: str = "", api_key: str = ""):
        llm = _llm_model_config()
        self._base = (base or _env("BAIZE_EMBEDDING_BASE_URL")
                      or (llm.base_url if llm else ""))
        # 模型不复用 LLM 的 ``model``：那是聊天模型，向量要的是 embedding 模型。
        # 取用顺序：显式传参 > 环境变量 > 配置文件 embedding_model > 内置默认。
        # 这样「启动时配置」能定模型，运维又可用环境变量临时覆盖而不用改文件。
        self._model = (model or _env("BAIZE_EMBEDDING_MODEL")
                       or (getattr(llm, "embedding_model", "") if llm else "")
                       or DEFAULT_EMBEDDING_MODEL)
        self._key = (api_key or _env("BAIZE_EMBEDDING_API_KEY")
                     or (llm.api_key if llm else "") or "sk-placeholder")
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cache: dict[str, list[float]] = {}
        self._fail_until = 0.0

    # ---- 状态 ---------------------------------------------------------------
    def configured(self) -> bool:
        return bool(self._base and self._model)

    def enabled(self) -> bool:
        """未配置、或处于熔断期，都为 False——调用方据此决定走不走语义通道。"""
        return self.configured() and time.time() >= self._fail_until

    def _trip(self) -> None:
        self._fail_until = time.time() + CIRCUIT_SECONDS
        logger.warning("embedding 不可用，%d 秒内降级为纯 BM25",
                       int(CIRCUIT_SECONDS))

    # ---- 请求 ---------------------------------------------------------------
    async def _request(self, texts: list[str]) -> Optional[list[list[float]]]:
        import httpx

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                _endpoint(self._base),
                json={"model": self._model, "input": texts},
                headers={"Authorization": f"Bearer {self._key}"},
            )
            resp.raise_for_status()
            items = (resp.json() or {}).get("data") or []
        if len(items) != len(texts):
            raise ValueError(f"embedding 返回 {len(items)} 条，期望 {len(texts)} 条")
        items.sort(key=lambda x: x.get("index", 0))
        return [list(it.get("embedding") or []) for it in items]

    async def embed_async(self, texts: list[str]) -> Optional[list[list[float]]]:
        """异步批量向量化，供写入侧补算文档向量（跑在调用方的事件循环里）。"""
        if not self.enabled() or not texts:
            return None
        try:
            return await self._request(list(texts))
        except Exception as exc:  # 网络 / 鉴权 / 返回格式都可能出错
            logger.warning("embedding 补算失败：%s", exc)
            self._trip()
            return None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever, name="baize-embed",
                    daemon=True)
                self._thread.start()
            return self._loop

    def embed_sync(self, texts: list[str],
                   timeout: float = DEFAULT_TIMEOUT) -> Optional[list[list[float]]]:
        """同步上下文里取向量：丢给后台线程的事件循环，阻塞等结果。

        检索路径没法 await，又不能在同步接口里 asyncio.run（调用方若已在事件
        循环里会直接抛错）。后台线程 + run_coroutine_threadsafe 是唯一既不阻塞
        主循环、又能同步等待的办法。
        """
        if not self.enabled() or not texts:
            return None
        try:
            loop = self._ensure_loop()
            fut = asyncio.run_coroutine_threadsafe(self._request(list(texts)), loop)
            return fut.result(timeout)
        except Exception as exc:
            logger.warning("embedding 查询失败，本次降级为纯 BM25：%s", exc)
            self._trip()
            return None

    def embed_one_sync(self, text: str,
                       timeout: float = DEFAULT_TIMEOUT) -> Optional[list[float]]:
        """单条向量化（带缓存）：查询文本重复率高，缓存能省掉大量往返。"""
        if not self.enabled() or not text.strip():
            return None
        key = fingerprint(text)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        vecs = self.embed_sync([text], timeout=timeout)
        if not vecs or not vecs[0]:
            return None
        if len(self._cache) >= _CACHE_MAX:
            self._cache.clear()
        self._cache[key] = vecs[0]
        return vecs[0]


_embedder: Optional[Embedder] = None
_embedder_lock = threading.Lock()


def embedder() -> Embedder:
    """进程内共享一个实例：后台线程与查询缓存都只应有一份。"""
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = Embedder()
        return _embedder


def _needs_recompute(exp: dict) -> tuple[str, str]:
    """返回（文本, 指纹）；内容没变返回空指纹，表示不必重算。"""
    text = experience_text(exp)
    fp = fingerprint(text)
    cur = exp.get("embedding") or {}
    if cur.get("fp") == fp and cur.get("v"):
        return text, ""
    return text, fp


async def attach_embedding(exp: dict) -> bool:
    """给一条经验补上向量块 ``{"fp": ..., "v": ...}``。

    返回 True 表示 exp 被改动、调用方需要回存。检索侧只在指纹对得上时才肯用
    这条向量（内容改过而向量没重算，宁可不用也不能用错），所以指纹必须和向量
    一起写。指纹一致即内容未动，直接跳过——这让它可以无脑插在每个写入点上，
    改状态的路径不会白白多一次 API 往返。

    算不出向量（未配置 / 服务不可用 / 熔断中）一律返回 False：写入不能因为
    向量失败而中断，检索会自动退回纯 BM25。
    """
    if not embedder().enabled():
        return False
    text, fp = _needs_recompute(exp)
    if not fp:
        return False
    vecs = await embedder().embed_async([text])
    if not vecs or not vecs[0]:
        return False
    exp["embedding"] = {"fp": fp, "v": vecs[0]}
    return True


def attach_embedding_sync(exp: dict) -> bool:
    """:func:`attach_embedding` 的同步版本，供非 async 写入路径与批量回填使用。"""
    if not embedder().enabled():
        return False
    text, fp = _needs_recompute(exp)
    if not fp:
        return False
    vecs = embedder().embed_sync([text])
    if not vecs or not vecs[0]:
        return False
    exp["embedding"] = {"fp": fp, "v": vecs[0]}
    return True
