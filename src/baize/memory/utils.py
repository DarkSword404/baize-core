"""通用小工具：ID / 时间 / 原子写 / 分词 / 摘要。

本模块属于全新 memory 子系统，与任何旧经验实现完全隔离。
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_EN_OR_DIGIT = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_PUNCT = set(" \t\r\n，。；：、（）()【】[]{}《》,.!?\"'")


def now_iso() -> str:
    """UTC ISO 时间（Z 结尾，可排序/比较）。"""
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            return None


def new_id(prefix: str = "m") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


_WRITE_LOCK = threading.RLock()


def atomic_write_text(path: Path, text: str) -> None:
    """原子写文本：先写 .tmp 再 os.replace，避免进程中断产生半截文件。"""
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)


def json_dump(path: Path, obj: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent))


def json_load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def data_root() -> Path:
    """记忆库根目录（真相源）。可用 BAIZE_MEMORY_DIR 覆盖。"""
    override = os.environ.get("BAIZE_MEMORY_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".baize" / "memory"


def excerpt(text: str, limit: int = 240) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def tokenize(text: str) -> list[str]:
    """中英混合分词：英文/数字按词，中文按 unigram + bigram。"""
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        m = _EN_OR_DIGIT.match(text, pos)
        if m:
            w = m.group(0).lower()
            if len(w) >= 2 or w.isdigit():
                tokens.append(w)
            pos = m.end()
            continue
        if text[pos] in _PUNCT:
            pos += 1
            continue
        run = _CJK_RUN.match(text, pos)
        if run:
            seg = run.group(0)
            tokens.extend(seg)
            if len(seg) >= 2:
                tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))
            pos = run.end()
            continue
        pos += 1
    return tokens


def token_dict(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tokenize(text):
        out[t] = out.get(t, 0) + 1
    return out
