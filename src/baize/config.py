"""Baize 配置管理。

支持通过环境变量和 ``~/.baize/model.json`` 配置单一模型
（base_url + api_key + model），以及 Web API 服务参数。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# 默认配置目录（用户级）
DEFAULT_BAIZE_DIR = Path.home() / ".baize"
MODEL_CONFIG_FILE = DEFAULT_BAIZE_DIR / "model.json"
AUTH_DB_FILE = DEFAULT_BAIZE_DIR / "api_auth.json"
GUARDRAILS_FILE = DEFAULT_BAIZE_DIR / "guardrails.json"


def _opt_int(value) -> int | None:
    """将可空整数配置归一化：None/空串 -> None，其余转 int。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class SingleModelConfig:
    """单一模型配置。

    ``context_max_turns``: 上下文滑动窗口轮数（0 表示不限制、保留全部历史）。
    每"一轮"指一条 user + assistant 的完整问答。

    ``context_window``: 模型上下文窗口总大小（token）。配置后用于自动推导
    token 预算（窗口 × 90%，预留输出空间），无需手动填写 ``max_context_tokens``。

    ``max_context_tokens``: 上下文 token 预算上限（None 使用内置默认
    256000 或按 ``context_window`` 推导，0 表示不限制）。超出预算时按
    摘要压缩 → 骨架压缩 → 最旧轮次丢弃的顺序处理，防止上下文超限报错
    （如 429 token-limit / insufficient_quota）。

    ``max_message_chars``: 单条消息最大字符数（None 使用内置默认 80000，
    0 表示不限制）。超长内容（如工具输出）自动截断保留头尾。

    ``enable_context_summary``: 超预算时是否用 LLM 把最旧的一批轮次
    压缩为语义摘要（比机械截断更保信息，但会多消耗一次模型调用）。
    """

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    context_max_turns: int = 0
    context_window: int | None = None
    max_context_tokens: int | None = None
    max_message_chars: int | None = None
    enable_context_summary: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)


class ModelConfigStore:
    """单模型配置的持久化存储。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or MODEL_CONFIG_FILE

    def load(self) -> SingleModelConfig | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            cfg = SingleModelConfig(
                base_url=str(data.get("base_url", "")),
                api_key=str(data.get("api_key", "")),
                model=str(data.get("model", "")),
                context_max_turns=int(data.get("context_max_turns", 0) or 0),
                context_window=_opt_int(data.get("context_window")),
                max_context_tokens=_opt_int(data.get("max_context_tokens")),
                max_message_chars=_opt_int(data.get("max_message_chars")),
                enable_context_summary=bool(data.get("enable_context_summary", False)),
            )
            return cfg if cfg.is_configured else None
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, cfg: SingleModelConfig) -> SingleModelConfig:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)
        return cfg

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


@dataclass
class ServerConfig:
    """Web API 服务配置。"""

    host: str = field(default_factory=lambda: os.getenv("BAIZE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("BAIZE_PORT", "8001")))
    require_auth: bool = field(
        default_factory=lambda: os.getenv("BAIZE_API_REQUIRE_AUTH", "1").lower()
        in ("1", "true", "yes")
    )
    # 前端 Vite 开发地址（用于凭证登录 URL 提示）
    frontend_url: str = field(default_factory=lambda: os.getenv("BAIZE_FRONTEND_URL", "http://localhost:5173"))


def get_server_config() -> ServerConfig:
    return ServerConfig()
