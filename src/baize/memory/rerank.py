"""可选的精排层（rerank / cross-encoder）。

召回层（BM25 + 向量 + 图）的共同特点是「候选与查询各自独立算分」，便宜但粗：
BM25 只看词面、向量只看句意、图只看实体距离，三者都拿不到「把查询与候选放
在一起看是否真的对得上」这个信号。精排层补的就是这一步——把两者拼在一起打
分。代价是必须对每个候选跑一次模型，因此只对召回后的少量候选生效（先粗召回
再精排，是检索系统的标准两段式）。

与 embedding 一样严格保持**可选**：未配置 rerank 服务时 enabled() 为 False，
精排退回检索侧本地的启发式信号，结果不比接入前差。这个降级不是可有可无的
——渗透场景常在内网离线跑，检索质量不该依赖某个外部服务是否可达。

同步等待的做法与 embed.py 同理：检索路径是同步的，不能在调用方可能已持有
事件循环时 asyncio.run；这里另起一个后台线程的事件循环，与 embedding 的
循环相互独立，rerank 超时不会拖累 embedding。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("baize.memory.rerank")

DEFAULT_TIMEOUT = 6.0
CIRCUIT_SECONDS = 300.0


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _endpoint(base: str) -> str:
    """把用户给的 base_url 归一到 rerank 端点。"""
    b = (base or "").rstrip("/")
    if b.endswith("/rerank"):
        return b
    if not b.endswith("/v1"):
        b += "/v1"
    return b + "/rerank"


def _pick_score(item: dict) -> float:
    """兼容不同服务的分数字段名。

    Jina / Infinity / Cohere 风格用 relevance_score，部分实现用 score。取不到
    就当 0，宁可让这条候选靠后，也不要因为字段名不对就整批失败。
    """
    for key in ("relevance_score", "score", "similarity"):
        v = item.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="baize-rerank",
                             daemon=True).start()
        return _loop


class Reranker:
    """OpenAI 兼容 /v1/rerank 的客户端；未配置即整体降级。"""

    def __init__(self, base: str = "", model: str = "", api_key: str = ""):
        self._base = base or _env("BAIZE_RERANK_BASE_URL")
        self._model = model or _env("BAIZE_RERANK_MODEL")
        self._key = api_key or _env("BAIZE_RERANK_API_KEY") or "sk-placeholder"
        self._fail_until = 0.0

    def configured(self) -> bool:
        return bool(self._base and self._model)

    def enabled(self) -> bool:
        return self.configured() and time.time() >= self._fail_until

    def _trip(self) -> None:
        self._fail_until = time.time() + CIRCUIT_SECONDS
        logger.warning("rerank 服务不可用，%d 秒内降级为本地启发式精排",
                       int(CIRCUIT_SECONDS))

    async def _request(self, query: str, docs: list[str]) -> Optional[list[float]]:
        import httpx

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                _endpoint(self._base),
                json={"model": self._model, "query": query, "documents": docs},
                headers={"Authorization": f"Bearer {self._key}"},
            )
            resp.raise_for_status()
            payload = resp.json() or {}
        items = payload.get("results") or payload.get("data") or []
        if len(items) != len(docs):
            raise ValueError(f"rerank 返回 {len(items)} 条，期望 {len(docs)} 条")
        if items and isinstance(items[0], dict) and "index" in items[0]:
            items = sorted(items, key=lambda x: x.get("index", 0))
        return [_pick_score(it) if isinstance(it, dict) else 0.0 for it in items]

    def rerank_sync(self, query: str, docs: list[str],
                    timeout: float = DEFAULT_TIMEOUT) -> Optional[list[float]]:
        """同步上下文里精排；失败返回 None 由调用方降级。"""
        if not self.enabled() or not query.strip() or not docs:
            return None
        try:
            loop = _ensure_loop()
            fut = asyncio.run_coroutine_threadsafe(
                self._request(query, list(docs)), loop)
            return fut.result(timeout)
        except Exception as exc:
            logger.warning("rerank 调用失败，本次降级为本地启发式：%s", exc)
            self._trip()
            return None


_reranker: Optional[Reranker] = None
_reranker_lock = threading.Lock()


def reranker() -> Reranker:
    global _reranker
    with _reranker_lock:
        if _reranker is None:
            _reranker = Reranker()
        return _reranker
