"""Baize 大模型客户端。

通过 ``openai`` 官方 SDK 直连任意 OpenAI 兼容端点，支持普通与流式对话。
这是 Baize 独立的推理核心，不依赖任何第三方 agent 框架。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

logger = logging.getLogger("baize.client")

import httpx
from openai import AsyncOpenAI

from baize.config import ModelConfigStore, SingleModelConfig

# LLM 端点的网络超时：connect 30s（容忍网络抖动），read 90s（长生成但需快速失败），
# 配合 openai SDK 的 max_retries 自动重试连接错误，提高流水线稳定性。
# read 从 180s 降到 90s：单轮 LLM 调用不应超过 90s，否则会拖垮 agent 300s 节点超时。
LLM_TIMEOUT = httpx.Timeout(connect=30.0, read=90.0, write=60.0, pool=30.0)
# 大 max_tokens 长生成调用专用（Reason 调度推理、报告章节正文）：思考型模型
# 深度推理单次常需 2-5 分钟，90s read 会误杀（SDK 重试后仍超时 → Reason 判失败
# → 整波调度停摆）。仅显式传入的 complete 调用使用，agent 轮次仍走 LLM_TIMEOUT
# （保护 300s 节点超时）。
LLM_LONG_TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0)
# 注意：Agent 层另有 _LLM_RETRY_ATTEMPTS=4 的业务重试（总 5 次尝试）。
# 两层相乘会把最坏请求数放大到 5×(1+3)=20 次，配合 90s 读超时，
# 单次 complete() 最坏可挂数十分钟 —— 远超注释里"覆盖约 15 秒抖动"的意图。
# 这里只保留 1 次连接层重试（快速失败），把重试决策交给上层：Agent 层能
# 判断哪些错误可重试（限流/5xx）、哪些是配置错误（401/404），语义更准确。
LLM_MAX_RETRIES = 1


def estimate_tokens(text: str) -> int:
    """粗略估算文本 token 数（用于预算裁剪，无需精确）。

    中文按 1 字符 ≈ 1 token，其他字符按 4 字符 ≈ 1 token。
    估算结果偏保守（偏大），保证不会低估上下文。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + math.ceil(other / 4)


class ModelNotConfiguredError(RuntimeError):
    """模型未配置时抛出。"""


@dataclass
class ChatMessage:
    role: str
    content: str
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    # 多模态：若设置，则 content 被忽略，使用 OpenAI 内容块数组
    # 例: [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:..."}}]
    content_parts: Optional[list[dict]] = None


@dataclass
class CompletionUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class CompletionResult:
    content: str
    usage: CompletionUsage = field(default_factory=CompletionUsage)
    finish_reason: Optional[str] = None
    tool_calls_delta: Optional[list[Any]] = None  # 流式工具调用增量
    tool_calls: Optional[list[Any]] = None  # 完整工具调用列表（非流式）
    reasoning: str = ""  # 模型思考过程（如 deepseek 的 reasoning_content 增量）
    reset: bool = False  # 断流恢复标记：上游已产出部分内容后断连，重试前先重置缓冲


def get_active_model_config() -> SingleModelConfig | None:
    """返回当前激活的单模型配置。"""
    store = ModelConfigStore()
    return store.load()


def resolve_model_config() -> SingleModelConfig:
    """解析有效模型配置，未配置则抛出异常。"""
    cfg = get_active_model_config()
    if cfg is None or not cfg.is_configured:
        raise ModelNotConfiguredError(
            "尚未配置模型。请在设置页填写 base_url / api_key / model，"
            "或编辑 ~/.baize/model.json。"
        )
    return cfg


class LLMClient:
    """基于 OpenAI 兼容端点的异步客户端。"""

    def __init__(self, config: Optional[SingleModelConfig] = None) -> None:
        self._config = config or resolve_model_config()
        self._client = AsyncOpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key or "sk-placeholder",
            timeout=LLM_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def _messages(self, history: list[ChatMessage]) -> list[dict]:
        out: list[dict] = []
        for m in history:
            item: dict[str, Any] = {"role": m.role}
            if m.content_parts:
                # 多模态内容块（含图片）
                item["content"] = m.content_parts
                part_types = [p.get("type") for p in m.content_parts]
                image_count = sum(1 for p in m.content_parts if p.get("type") == "image_url")
                logger.info(
                    "[client] 多模态消息: role=%s, parts=%s, images=%d",
                    m.role, part_types, image_count,
                )
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
        timeout: Optional[httpx.Timeout] = None,
    ) -> CompletionResult:
        """非流式对话补全（支持工具调用解析）。

        timeout=None 时使用客户端默认 LLM_TIMEOUT；长推理/长文生成调用方可传入
        LLM_LONG_TIMEOUT 覆盖。
        """
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(history),
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ""
        # 解析工具调用（OpenAI 格式）
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
        # ── 空内容 / 截断统一检测 ──
        # 上游返回 200 但 content 为空时，调用方通常直接拿空串静默降级，
        # 排查成本极高（表现为"编排空转""回复是空的"）。这里统一告警并带上
        # finish_reason 与 usage（含 reasoning_tokens）以便定性：
        #   finish=length / reasoning 占满 → 输出预算被思考吃光
        #   finish=content_filter        → 上游内容过滤
        finish_reason = choice.finish_reason
        if not content.strip() and not tool_calls:
            logger.warning(
                "LLM 返回空内容: model=%s finish=%s input=%d output=%d reasoning=%d",
                self.model, finish_reason,
                usage.input_tokens, usage.output_tokens, usage.reasoning_tokens,
            )
        elif finish_reason == "length":
            logger.warning(
                "LLM 输出被截断(finish=length): model=%s content_len=%d "
                "output=%d reasoning=%d —— 考虑调大 max_tokens",
                self.model, len(content),
                usage.output_tokens, usage.reasoning_tokens,
            )
        return CompletionResult(
            content=content,
            usage=usage,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        history: list[ChatMessage],
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[CompletionResult]:
        """流式对话补全，逐增量产出。

        ``max_tokens``：此前 ``stream()`` 根本没有该参数，调用方无法为流式请求
        设置输出预算。思考型模型的 reasoning token 计入该预算，完全不设时由
        服务端默认值决定，容易出现正文被思考挤没（返回空 content）的情况。
        """
        extra: dict[str, Any] = {}
        if max_tokens is not None:
            extra["max_tokens"] = max_tokens
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(history),
            tools=tools,
            temperature=temperature,
            stream=True,
            **extra,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            content = delta.content if delta else ""
            # 模型思考过程（deepseek 等推理模型通过 reasoning_content 返回）
            reasoning = getattr(delta, "reasoning_content", None) or ""
            tool_calls = delta.tool_calls if delta else None
            usage = getattr(chunk, "usage", None)
            # 注意：content / reasoning / tool_calls 可能出现在同一个 chunk 中
            # （推理模型常见），必须用独立 if 逐个处理，否则工具调用增量会被
            # content 分支吞掉，导致模型想继续调用工具时增量丢失、智能体提前结束。
            if content:
                yield CompletionResult(content=content)
            if reasoning:
                # 实时思考增量：透传给前端展示，不作为最终回复
                yield CompletionResult(content="", reasoning=reasoning)
            if tool_calls:
                # 流式工具调用增量
                yield CompletionResult(content="", tool_calls_delta=tool_calls)
