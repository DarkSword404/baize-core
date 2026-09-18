"""TokenJuice 风格工具输出语义压缩器。

参考 OpenHuman 的 TokenJuice 方案：对大体积工具输出先做 LLM 语义摘要，
保留关键信息的同时将 token 数压缩 80%+，避免简单截断丢失重要数据。

设计要点：
- 仅当输出超过阈值（默认 4000 字符）时触发压缩。
- 压缩 LLM 可独立配置（默认复用主模型），使用极短 prompt。
- 压缩失败时自动回退，原样返回原始输出（不阻塞任务）。
- 保留渗透测试关键信息：URL、IP、端口、漏洞名、文件路径、CVE 编号、错误信息。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("baize.compressor")

# ---- 压缩 prompt ----------------------------------------------------------
# 目标：把大体积工具输出压缩为 < 1/5 长度的结构化摘要，保留渗透关键信息。

_COMPRESS_SYSTEM = (
    "你是一个安全工具输出压缩器。把大体积输出压缩为简短的结构化摘要，"
    "保留所有安全关键信息，丢弃冗余的格式化、重复内容和无关输出。"
)

_COMPRESS_TEMPLATE = (
    "工具名称：{tool_name}\n\n"
    "原始输出（共 {original_len} 字符）：\n{output}\n\n"
    "请压缩为以下格式，不超过 5 条发现，每条一行：\n"
    "【关键发现】\n"
    "- 发现1（保留 URL / IP / 端口 / 漏洞名 / CVE / 路径 / 版本号 / 关键数据）\n"
    "- 发现2\n"
    "...\n"
    "【摘要】一句话总结\n"
    "【重要数据】保留原始输出中最重要的 3-5 个具体数值/路径/标识符"
)

# ---- 默认配置 --------------------------------------------------------------

DEFAULT_COMPRESS_CHAR_THRESHOLD = 4000  # 字符数超过该值才触发压缩
DEFAULT_COMPRESS_TARGET_TOKENS = 800    # 压缩后目标 token 上限（约 600 中文字符）


@dataclass
class CompressorConfig:
    """压缩器配置。"""

    enabled: bool = True
    """是否启用工具输出压缩。"""

    char_threshold: int = DEFAULT_COMPRESS_CHAR_THRESHOLD
    """输出字符数超过该值才触发压缩。"""

    model: Optional[str] = None
    """压缩专用模型名（None 表示复用 Agent 的主模型）。"""

    model_provider: Optional[str] = None
    """压缩专用模型提供方（None 表示复用 Agent 的主 model_provider）。"""

    max_compressed_chars: int = 2000
    """压缩后最大字符数（超出则截断并标注）。"""

    exclude_tools: list[str] = field(default_factory=list)
    """不压缩的工具名列表（共享浏览器截图等短输出/高价值图像数据）。"""


class ToolOutputCompressor:
    """工具输出语义压缩器。

    用法::

        comp = ToolOutputCompressor(config)
        output = await comp.compress("nmap_scan", raw_output)
    """

    def __init__(self, config: Optional[CompressorConfig] = None):
        self._config = config or CompressorConfig()

    # ---- 公共 API ----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def should_compress(self, tool_name: str, output: str) -> bool:
        """判断是否需要对给定工具输出进行压缩。"""
        if not self._config.enabled:
            return False
        if tool_name in self._config.exclude_tools:
            return False
        return len(output) > self._config.char_threshold

    async def compress(
        self,
        tool_name: str,
        output: str,
        *,
        model: Optional[str] = None,
        model_provider: Optional[str] = None,
    ) -> str:
        """压缩工具输出。

        参数:
            tool_name: 工具名称。
            output: 原始工具输出文本。
            model: 覆盖压缩模型（None 用配置默认）。
            model_provider: 覆盖压缩模型提供方。

        返回:
            压缩后的文本。压缩失败时返回原始输出。
        """
        if not self.should_compress(tool_name, output):
            return output

        effective_model = model or self._config.model
        effective_provider = model_provider or self._config.model_provider

        try:
            compressed = await self._compress_via_llm(
                tool_name, output, effective_model, effective_provider
            )
            if compressed:
                original_len = len(output)
                compressed_len = len(compressed)
                ratio = f"{compressed_len / max(original_len, 1) * 100:.0f}%"
                logger.info(
                    "工具输出压缩: %s %d→%d 字符 (%s)",
                    tool_name, original_len, compressed_len, ratio,
                )
                return compressed
        except Exception as exc:
            logger.warning("工具输出压缩失败 (%s): %s，回退为原始输出", tool_name, exc)

        return output

    async def _compress_via_llm(
        self,
        tool_name: str,
        output: str,
        model: Optional[str],
        model_provider: Optional[str],
    ) -> Optional[str]:
        """调用 LLM 执行语义压缩。"""
        from baize.sdk.client import LLMClient, SingleModelConfig, resolve_model_config, ChatMessage

        # 如果指定了独立模型，构建对应配置；否则复用全局模型配置
        if model:
            base_config = resolve_model_config()
            config = SingleModelConfig(
                base_url=base_config.base_url,
                api_key=base_config.api_key,
                model=model,
                context_window=base_config.context_window,
            )
            client = LLMClient(config=config)
        else:
            # 复用主模型配置
            client = LLMClient()

        prompt = _COMPRESS_TEMPLATE.format(
            tool_name=tool_name,
            original_len=len(output),
            output=output,
        )

        messages = [
            ChatMessage(role="system", content=_COMPRESS_SYSTEM),
            ChatMessage(role="user", content=prompt),
        ]

        result = await client.complete(messages, tools=None)
        text = (result.content or "").strip()

        if not text:
            return None

        # 防御：压缩结果比原文还长或接近原文 → 不采用
        if len(text) >= len(output) * 0.9:
            logger.debug("压缩输出 ≥ 原文 90%%，放弃压缩")
            return None

        # 截断超长压缩结果
        max_chars = self._config.max_compressed_chars
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...(压缩结果过长，已截断)"

        return text