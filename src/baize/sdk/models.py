"""Baize 模型层抽象（BaseChatModel + Provider 适配器 + 路由/Fallback）。

对齐 LangChain 的 ``BaseChatModel`` 设计:
- ``BaseChatModel``: 统一的聊天模型接口（``complete`` / ``stream``）。
- ``OpenAICompatibleModel``: OpenAI 兼容端点的适配器（默认实现，等价旧 LLMClient）。
- ``ModelRegistry``: 按名称注册/查询模型提供方。
- ``ModelRouter``: 多模型路由（按名称切换 + 失败 fallback 链）。

旧 ``LLMClient`` 保持向后兼容（作为 OpenAICompatibleModel 的别名）。
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from openai import AsyncOpenAI

from baize.config import ModelConfigStore, SingleModelConfig
from baize.sdk.client import (
    ChatMessage,
    CompletionResult,
    CompletionUsage,
    ModelNotConfiguredError,
    resolve_model_config,
)

logger = logging.getLogger("baize.models")


# ===========================================================================
#  BaseChatModel — 统一模型接口
# ===========================================================================

class BaseChatModel(abc.ABC):
    """聊天模型抽象基类。

    所有 Provider 适配器实现 ``complete`` 与 ``stream``，
    上层 Agent 只依赖此接口，从而支持任意模型 / 本地部署 / 多模型切换。
    """

    name: str = "base"
    model: str = ""

    @abc.abstractmethod
    async def complete(
        self,
        history: list[ChatMessage],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> CompletionResult:
        """非流式对话补全（含工具调用解析）。"""

    @abc.abstractmethod
    async def stream(
        self,
        history: list[ChatMessage],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[CompletionResult]:
        """流式对话补全，逐增量产出。"""

    async def with_tool(self, tools: Optional[list[dict]]) -> None:
        """预留：模型能力探测（如工具调用支持）。"""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} name={self.name} model={self.model}>"


# ===========================================================================
#  OpenAICompatibleModel — OpenAI 兼容端点适配器（默认实现）
# ===========================================================================

class OpenAICompatibleModel(BaseChatModel):
    """OpenAI 兼容端点适配器。

    支持任何实现 OpenAI Chat Completions API 的端点
    （OpenAI / DeepSeek / Ollama / vLLM / 各类国内大模型网关）。
    """

    name = "openai-compatible"

    def __init__(self, config: Optional[SingleModelConfig] = None) -> None:
        self._config = config or resolve_model_config()
        self.model = self._config.model
        self._client = AsyncOpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key or "sk-placeholder",
        )

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def _messages(self, history: list[ChatMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in history:
            item: dict[str, Any] = {"role": m.role}
            if m.content_parts:
                item["content"] = m.content_parts
            elif m.content:
                item["content"] = m.content
            if m.tool_calls:
                item["tool_calls"] = m.tool_calls
            if m.tool_call_id:
                item["tool_call_id"] = m.tool_call_id
            if m.name:
                item["name"] = m.name
            out.append(item)
        return out

    async def complete(
        self,
        history: list[ChatMessage],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> CompletionResult:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(history),
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ""
        tool_calls = None
        if getattr(msg, "tool_calls", None):
            tool_calls = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in msg.tool_calls
            ]
        usage = CompletionUsage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
            reasoning_tokens=getattr(getattr(resp.usage, "completion_tokens_details", None), "reasoning_tokens", 0) or 0,
        )
        return CompletionResult(
            content=content,
            usage=usage,
            finish_reason=choice.finish_reason,
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        history: list[ChatMessage],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[CompletionResult]:
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(history),
            tools=tools,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            content = delta.content if delta else ""
            reasoning = getattr(delta, "reasoning_content", None) or ""
            tool_calls = delta.tool_calls if delta else None
            if content:
                yield CompletionResult(content=content)
            if reasoning:
                yield CompletionResult(content="", reasoning=reasoning)
            if tool_calls:
                yield CompletionResult(content="", tool_calls_delta=tool_calls)


# ===========================================================================
#  兼容层：LLMClient 保留为 OpenAICompatibleModel 的别名
# ===========================================================================

class LLMClient(OpenAICompatibleModel):
    """兼容旧 API 的模型客户端（行为与 baize.sdk.client.LLMClient 一致）。"""


# ===========================================================================
#  ModelRegistry — 模型提供方注册表
# ===========================================================================

@dataclass
class ModelSpec:
    """模型提供方定义。"""

    name: str  # 注册名（如 "deepseek"、"ollama"、"primary"）
    factory: Any  # 工厂 callable: (config) -> BaseChatModel
    description: str = ""


class ModelRegistry:
    """模型注册表 —— 按名称注册 / 查询模型提供方。"""

    def __init__(self) -> None:
        self._models: dict[str, ModelSpec] = {}

    def register(
        self,
        name: str,
        factory: Any,
        description: str = "",
        *,
        override: bool = False,
    ) -> None:
        if name in self._models and not override:
            raise ValueError(f"模型 '{name}' 已注册")
        self._models[name] = ModelSpec(name=name, factory=factory, description=description)

    def get(self, name: str) -> Optional[ModelSpec]:
        return self._models.get(name)

    def names(self) -> list[str]:
        return list(self._models.keys())

    def create(self, name: str, config: Optional[SingleModelConfig] = None) -> BaseChatModel:
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"模型 '{name}' 未注册")
        return spec.factory(config)


# 全局模型注册表（默认注册 OpenAI 兼容适配器）
model_registry = ModelRegistry()
model_registry.register("openai-compatible", OpenAICompatibleModel, "OpenAI 兼容端点")


# ===========================================================================
#  ModelRouter — 多模型路由 + Fallback
# ===========================================================================

@dataclass
class ModelRouter:
    """多模型路由 —— 支持按名称切换与失败 fallback。

    Attributes:
        primary: 首选模型提供方名（注册表中的名称）。
        fallbacks: 后备模型提供方列表（按顺序尝试）。
        create_model: 创建模型的工厂（默认从全局注册表创建）。
    """

    primary: str = "openai-compatible"
    fallbacks: list[str] = field(default_factory=list)
    create_model: Any = None

    def __post_init__(self) -> None:
        if self.create_model is None:
            self.create_model = lambda name: model_registry.create(name)

    def _resolve(self, name: Optional[str] = None) -> str:
        """确定要使用的模型提供方名。"""
        if name:
            return name
        return self.primary

    def model(self, name: Optional[str] = None) -> BaseChatModel:
        """创建指定名称（或首选）的模型实例。"""
        resolved = self._resolve(name)
        try:
            return self.create_model(resolved)
        except Exception as exc:  # noqa: BLE001
            raise ModelNotConfiguredError(f"模型 '{resolved}' 创建失败: {exc}") from exc

    async def complete(
        self,
        history: list[ChatMessage],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        name: Optional[str] = None,
    ) -> CompletionResult:
        """按路由顺序调用，失败时 fallback 到后备模型。"""
        candidates = []
        primary = self._resolve(name)
        candidates.append(primary)
        candidates.extend(f for f in self.fallbacks if f != primary)

        last_exc: Optional[Exception] = None
        for cand in candidates:
            try:
                model = self.create_model(cand)
                logger.debug("ModelRouter 尝试模型: %s", cand)
                return await model.complete(
                    history,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("模型 %s 调用失败: %s，尝试下一个", cand, exc)

        raise ModelNotConfiguredError(f"所有模型均调用失败: {last_exc}")

    async def stream(
        self,
        history: list[ChatMessage],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        name: Optional[str] = None,
    ) -> AsyncIterator[CompletionResult]:
        """流式调用（失败时 fallback 到后备模型）。"""
        candidates = []
        primary = self._resolve(name)
        candidates.append(primary)
        candidates.extend(f for f in self.fallbacks if f != primary)

        last_exc: Optional[Exception] = None
        for cand in candidates:
            try:
                model = self.create_model(cand)
                logger.debug("ModelRouter 流式尝试模型: %s", cand)
                async for chunk in model.stream(history, tools=tools, temperature=temperature):
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("模型 %s 流式失败: %s，尝试下一个", cand, exc)

        raise ModelNotConfiguredError(f"所有模型均流式失败: {last_exc}")


__all__ = [
    "BaseChatModel",
    "OpenAICompatibleModel",
    "LLMClient",
    "ModelSpec",
    "ModelRegistry",
    "model_registry",
    "ModelRouter",
]
