"""Baize 多模态处理。

把会话附件转换为 LLM 消息内容块：
- 图片附件 → ``image_url`` 内容块（直接注入模型视觉）。
- 代码 / 文档 / 压缩包 / 其他 → 在文本中注入附件清单提示，并依赖 Agent 工具按需读取。
- 工具输出中的图片文件路径 → 自动提取并注入 LLM 上下文。

同时生成"附件描述文本"，让 Agent 知道当前会话有哪些附件可用。
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
from pathlib import Path
from typing import Optional

from baize.sdk.client import ChatMessage

logger = logging.getLogger("baize.multimodal")

MAX_IMAGES_PER_MESSAGE = 4  # 单条消息最多注入图片数（控制 token）
MAX_IMAGE_DATA_URL_BYTES = 5 * 1024 * 1024  # 单张图片最大 5MB（data URL 约 1.37x 原始大小）

# 工具输出中图片文件路径的匹配模式
_IMAGE_PATH_PATTERN = re.compile(
    r'(?:file://)?(/[\w/.\-\[\]()]+\.(?:png|jpg|jpeg|gif|webp|bmp))',
    re.IGNORECASE,
)


def extract_images_from_tool_output(text: str, max_images: int = MAX_IMAGES_PER_MESSAGE) -> list[dict]:
    """扫描工具输出文本中的图片文件路径，返回 ``image_url`` 内容块列表。

    只返回磁盘上确实存在且大小在限制内的图片文件。
    返回的列表可直接注入 ``ChatMessage.content_parts``。
    """
    if not text:
        return []

    candidates = _IMAGE_PATH_PATTERN.findall(text)
    if not candidates:
        return []

    image_blocks: list[dict] = []
    seen: set[str] = set()

    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if len(image_blocks) >= max_images:
            break

        p = Path(path)
        if not p.is_file():
            continue
        if p.stat().st_size > MAX_IMAGE_DATA_URL_BYTES:
            logger.info("图片过大，跳过注入: %s (%d bytes)", path, p.stat().st_size)
            continue

        try:
            mime, _ = mimetypes.guess_type(path)
            mime = mime or "image/png"
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            url = f"data:{mime};base64,{b64}"
            image_blocks.append(
                {"type": "image_url", "image_url": {"url": url}}
            )
            logger.info(
                "工具输出图片注入: path=%s, mime=%s, url_len=%d",
                path, mime, len(url),
            )
        except OSError as e:
            logger.warning("工具输出图片读取失败: %s (%s)", path, e)

    return image_blocks


def build_tool_image_message(output: str, max_images: int = MAX_IMAGES_PER_MESSAGE) -> Optional[ChatMessage]:
    """从工具输出中提取图片，构造一条多模态用户消息注入 LLM 上下文。

    若工具输出中无有效图片，返回 None。
    """
    image_blocks = extract_images_from_tool_output(output, max_images)
    if not image_blocks:
        return None

    parts: list[dict] = [
        {
            "type": "text",
            "text": (
                "[系统提示] 工具执行过程中产生了以下图片。"
                "请仔细查看图片内容（包括其中的文字、界面、图形等），"
                "并结合图片信息继续完成任务。"
            ),
        }
    ]
    parts.extend(image_blocks)
    logger.info(
        "工具图片消息构造完成: image_count=%d, total_parts=%d",
        len(image_blocks), len(parts),
    )
    return ChatMessage(role="user", content="", content_parts=parts)


def build_attachment_prompt(attachments: list) -> str:
    """生成附件清单文本（注入消息正文，提示 Agent 可用工具读取）。"""
    if not attachments:
        return ""
    lines = ["\n[会话附件] 用户上传了以下附件，可按需使用工具读取："]
    for a in attachments:
        lines.append(f"- {a.filename} (id={a.file_id}, 类型={a.file_type})")
    lines.append("可用附件工具: read_attachment_file, extract_attachment_archive, "
                 "read_extracted_file")
    return "\n".join(lines)


def build_user_message(
    text: str,
    attachments: list,
    *,
    attachment_store=None,
    session_id: Optional[str] = None,
) -> ChatMessage:
    """构造用户消息，图片注入内容块，其它附件注入文本提示。

    attachments: list[Attachment] 或 list[dict]，需含 file_id/filename/file_type。
    """
    def _ft(a) -> str:
        # 兼容 Attachment 对象（有 file_type 属性）和 dict
        return getattr(a, "file_type", None) or (a.get("file_type") if isinstance(a, dict) else "")

    def _fid(a) -> str:
        # 安全取 file_id（避免 getattr 的 default 立即求值问题）
        if isinstance(a, dict):
            return a.get("file_id", "")
        return getattr(a, "file_id", "")

    # 分离图片与其它附件
    images = [a for a in attachments if _ft(a) == "image"]
    others = [a for a in attachments if _ft(a) != "image"]

    text_parts: list[str] = [text]
    image_blocks: list[dict] = []

    # 图片 → image_url 内容块
    for img in images[:MAX_IMAGES_PER_MESSAGE]:
        if attachment_store is not None and session_id:
            fid = _fid(img)
            url = attachment_store.read_image_as_data_url(session_id, fid)
            if url:
                image_blocks.append(
                    {"type": "image_url", "image_url": {"url": url}}
                )
                logger.info(
                    "图片附件注入: file_id=%s, url_len=%d, url_prefix=%s",
                    fid, len(url), url[:80],
                )
            else:
                logger.warning("图片附件读取失败: file_id=%s, session_id=%s", fid, session_id)

    # 其它附件 → 文本提示
    if others:
        prompt = build_attachment_prompt(others)
        text_parts.append(prompt)

    text_content = "\n".join(text_parts)

    if image_blocks:
        # 多模态：文本 + 图片内容块
        parts: list[dict] = [{"type": "text", "text": text_content}]
        parts.extend(image_blocks)
        logger.info(
            "多模态消息构造完成: text_len=%d, image_count=%d, total_parts=%d",
            len(text_content), len(image_blocks), len(parts),
        )
        return ChatMessage(role="user", content="", content_parts=parts)

    return ChatMessage(role="user", content=text_content)
