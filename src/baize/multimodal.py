"""Baize 多模态处理。

把会话附件转换为 LLM 消息内容块：
- 图片附件 → ``image_url`` 内容块（直接注入模型视觉）。
- 代码 / 文档 / 压缩包 / 其他 → 在文本中注入附件清单提示，并依赖 Agent 工具按需读取。

同时生成"附件描述文本"，让 Agent 知道当前会话有哪些附件可用。
"""

from __future__ import annotations

from typing import Optional

from baize.sdk.client import ChatMessage

MAX_IMAGES_PER_MESSAGE = 4  # 单条消息最多注入图片数（控制 token）


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

    # 其它附件 → 文本提示
    if others:
        prompt = build_attachment_prompt(others)
        text_parts.append(prompt)

    text_content = "\n".join(text_parts)

    if image_blocks:
        # 多模态：文本 + 图片内容块
        parts: list[dict] = [{"type": "text", "text": text_content}]
        parts.extend(image_blocks)
        return ChatMessage(role="user", content="", content_parts=parts)

    return ChatMessage(role="user", content=text_content)
