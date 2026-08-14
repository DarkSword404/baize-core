"""Baize 附件存储与管理（多模态能力）。

负责会话附件：类型识别、安全存储、按类型读取（图片转 base64 / 文档提取文本 /
压缩包解压索引 / 代码读取），以及路径沙箱校验。

附件持久化目录：``~/.baize/sessions/{session_id}/files/``
"""

from __future__ import annotations

import base64
import io
import json
import re
import secrets
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from baize.config import DEFAULT_BAIZE_DIR
from baize.sdk.agent import AgentTool

# ----------------------------------------------------------------------
# 类型识别
# ----------------------------------------------------------------------

# 图片（直接注入 LLM）
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# 代码 / 文本（可按行读取）
CODE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".c", ".cpp", ".h", ".hpp",
    ".java", ".go", ".rs", ".rb", ".php", ".sh", ".bash", ".zsh",
    ".sql", ".html", ".htm", ".css", ".json", ".xml", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".txt", ".md", ".log", ".csv",
    ".kt", ".swift", ".cs", ".pl", ".lua", ".r", ".ipynb",
}

# 文档（需解析提取文本）
DOCUMENT_EXTS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}

# 压缩包（解压索引）
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tar.gz", ".tgz", ".bz2", ".tar.bz2", ".7z"}

# 安全：上传文件类型白名单（其它一律拒绝）
ALLOWED_EXTS = IMAGE_EXTS | CODE_EXTS | DOCUMENT_EXTS | ARCHIVE_EXTS | {".bin", ".elf", ".pcap", ".cap"}

# 单文件大小上限（默认 20MB）
DEFAULT_MAX_SIZE = 20 * 1024 * 1024
# 压缩包解压后最大总大小（防 zip 炸弹）
MAX_EXTRACT_TOTAL = 100 * 1024 * 1024
# 压缩包内最大文件数
MAX_EXTRACT_FILES = 200
# 单文件文本读取上限（避免撑爆上下文，单位字节）
MAX_TEXT_READ = 512 * 1024


def detect_file_type(filename: str) -> str:
    """根据扩展名识别附件类型。返回 image/code/document/archive/other。"""
    name = filename.lower()
    if name.endswith((".tar.gz", ".tar.bz2", ".tgz")):
        return "archive"
    ext = Path(name).suffix
    if ext in IMAGE_EXTS:
        return "image"
    if ext in CODE_EXTS:
        return "code"
    if ext in DOCUMENT_EXTS:
        return "document"
    if ext in ARCHIVE_EXTS:
        return "archive"
    return "other"


def is_allowed(filename: str) -> bool:
    name = filename.lower()
    if name.endswith((".tar.gz", ".tar.bz2", ".tgz")):
        return True
    return Path(name).suffix.lower() in ALLOWED_EXTS


def _safe_join(root: Path, rel: str) -> Path | None:
    """确保 rel 解析后仍在 root 内（防目录穿越）。"""
    root = root.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
        return target
    except ValueError:
        return None


@dataclass
class Attachment:
    """一个会话附件。"""

    file_id: str
    filename: str
    file_type: str  # image/code/document/archive/other
    mime: str = ""
    size: int = 0
    path: str = ""  # 存储路径（绝对）
    uploaded_at: str = ""

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "file_type": self.file_type,
            "mime": self.mime,
            "size": self.size,
            "uploaded_at": self.uploaded_at,
        }


class AttachmentStore:
    """会话附件的存储、读取与安全管理。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or DEFAULT_BAIZE_DIR / "sessions"
        self._base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 路径辅助
    # ------------------------------------------------------------------
    def _session_dir(self, session_id: str) -> Path:
        d = self._base_dir / session_id / "files"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _file_dir(self, session_id: str, file_id: str) -> Path:
        return self._session_dir(session_id) / file_id

    def _index_path(self, session_id: str) -> Path:
        return self._base_dir / session_id / "files_index.json"

    # ------------------------------------------------------------------
    # 上传与索引
    # ------------------------------------------------------------------
    def save_attachment(
        self,
        session_id: str,
        filename: str,
        data: bytes,
    ) -> Attachment:
        """保存上传的附件并登记到会话索引。"""
        if len(data) > DEFAULT_MAX_SIZE:
            raise ValueError(f"附件超过大小限制（{DEFAULT_MAX_SIZE // 1024 // 1024}MB）")
        if not is_allowed(filename):
            raise ValueError(f"不支持的文件类型: {filename}")

        # 路径穿越防护：仅保留文件名（basename），拒绝目录成分
        safe_name = Path(filename.replace("\\", "/")).name.strip()
        if not safe_name or safe_name in (".", ".."):
            raise ValueError(f"非法文件名: {filename}")

        file_id = secrets.token_hex(8)
        fdir = self._file_dir(session_id, file_id)
        fdir.mkdir(parents=True, exist_ok=True)

        # 原始文件（_safe_join 双重保险，确保写入路径在沙箱目录内）
        orig_dir = fdir / "original"
        orig = _safe_join(orig_dir, safe_name)
        if orig is None:
            raise ValueError(f"非法文件名: {filename}")
        orig.parent.mkdir(parents=True, exist_ok=True)
        orig.write_bytes(data)

        file_type = detect_file_type(filename)
        mime = IMAGE_MIME.get(Path(filename.lower()).suffix, "")
        att = Attachment(
            file_id=file_id,
            filename=filename,
            file_type=file_type,
            mime=mime,
            size=len(data),
            path=str(orig),
        )
        # 登记索引
        index = self._load_index(session_id)
        index[file_id] = att.to_dict()
        self._save_index(session_id, index)
        return att

    def _load_index(self, session_id: str) -> dict:
        p = self._index_path(session_id)
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_index(self, session_id: str, index: dict) -> None:
        self._index_path(session_id).write_text(
            json.dumps(index, ensure_ascii=False, indent=2), "utf-8"
        )

    def list_attachments(self, session_id: str) -> list[Attachment]:
        index = self._load_index(session_id)
        return [Attachment(**v) for v in index.values()]

    def get_attachment(self, session_id: str, file_id: str) -> Attachment | None:
        index = self._load_index(session_id)
        d = index.get(file_id)
        if d is None:
            return None
        return Attachment(**d)

    def original_path(self, session_id: str, file_id: str) -> Path | None:
        att = self.get_attachment(session_id, file_id)
        if att is None:
            return None
        # 原始文件路径由存储结构重新计算（不依赖持久化的绝对路径）。
        # 索引中保存的是上传时的原始 filename，可能包含目录成分；
        # 必须清洗为 basename 并再次 _safe_join，防止目录穿越读取沙箱外文件。
        safe_name = Path(att.filename.replace("\\", "/")).name.strip()
        if not safe_name or safe_name in (".", ".."):
            return None
        p = _safe_join(self._file_dir(session_id, file_id) / "original", safe_name)
        if p is None or not p.exists():
            return None
        return p

    def delete_attachment(self, session_id: str, file_id: str) -> bool:
        index = self._load_index(session_id)
        if file_id not in index:
            return False
        del index[file_id]
        self._save_index(session_id, index)
        fdir = self._file_dir(session_id, file_id)
        if fdir.exists():
            import shutil

            shutil.rmtree(fdir, ignore_errors=True)
        return True

    def delete_session(self, session_id: str) -> None:
        """删除会话的所有附件（连同索引）。"""
        idx = self._index_path(session_id)
        if idx.exists():
            idx.unlink()
        sdir = self._base_dir / session_id
        if sdir.exists():
            import shutil

            shutil.rmtree(sdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 按类型读取（供 Agent 工具与图片注入使用）
    # ------------------------------------------------------------------
    def read_image_as_data_url(self, session_id: str, file_id: str) -> str | None:
        """读取图片，返回 data:image/xxx;base64,... URL（供 LLM image_url）。"""
        att = self.get_attachment(session_id, file_id)
        if att is None or att.file_type != "image":
            return None
        p = self.original_path(session_id, file_id)
        if p is None:
            return None
        mime = att.mime or "image/png"
        try:
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except OSError:
            return None

    def read_text_content(self, session_id: str, file_id: str, limit: int = MAX_TEXT_READ) -> str:
        """读取代码/文本类附件内容（截断保护）。"""
        att = self.get_attachment(session_id, file_id)
        if att is None:
            return "(附件不存在)"
        p = self.original_path(session_id, file_id)
        if p is None:
            return "(附件文件缺失)"
        try:
            raw = p.read_bytes()[:limit]
            text = raw.decode("utf-8", errors="replace")
            if len(raw) >= limit:
                text += "\n...(内容过长，已截断)"
            return text
        except OSError as e:
            return f"(读取失败: {e})"

    def extract_archive(self, session_id: str, file_id: str) -> dict:
        """解压压缩包并返回文件清单。返回 {ok, entries: [{name, size}], error}。"""
        att = self.get_attachment(session_id, file_id)
        if att is None or att.file_type != "archive":
            return {"ok": False, "error": "不是压缩包类型", "entries": []}
        p = self.original_path(session_id, file_id)
        if p is None:
            return {"ok": False, "error": "附件文件缺失", "entries": []}

        extract_dir = self._file_dir(session_id, file_id) / "extracted"
        entries: list[dict] = []
        total_size = 0
        count = 0
        name = p.name.lower()

        try:
            if name.endswith(".zip") or zipfile.is_zipfile(p):
                with zipfile.ZipFile(p) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        count += 1
                        if count > MAX_EXTRACT_FILES:
                            break
                        # 防护：解压总大小限制
                        total_size += info.file_size
                        if total_size > MAX_EXTRACT_TOTAL:
                            return {"ok": False, "error": "压缩包过大，已中止", "entries": entries}
                        safe_path = _safe_join(extract_dir, info.filename)
                        if safe_path is None:
                            continue
                        safe_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, open(safe_path, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1 << 16)
                        entries.append({"name": info.filename, "size": info.file_size})
            elif name.endswith((".tar.gz", ".tar.bz2", ".tgz", ".tar")):
                mode = "r:gz" if name.endswith((".tar.gz", ".tgz")) else (
                    "r:bz2" if name.endswith(".tar.bz2") else "r"
                )
                with tarfile.open(p, mode) as tf:
                    for member in tf.getmembers():
                        if not member.isfile():
                            continue
                        count += 1
                        if count > MAX_EXTRACT_FILES:
                            break
                        total_size += member.size
                        if total_size > MAX_EXTRACT_TOTAL:
                            return {"ok": False, "error": "压缩包过大，已中止", "entries": entries}
                        safe_path = _safe_join(extract_dir, member.name)
                        if safe_path is None:
                            continue
                        safe_path.parent.mkdir(parents=True, exist_ok=True)
                        with tf.extractfile(member) as src, open(safe_path, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1 << 16)
                        entries.append({"name": member.name, "size": member.size})
            else:
                return {"ok": False, "error": "不支持的压缩格式", "entries": []}
            return {"ok": True, "entries": entries, "extract_dir": str(extract_dir)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"解压失败: {e}", "entries": entries}

    def read_extracted_file(self, session_id: str, file_id: str, rel_path: str) -> str:
        """读取解压目录中的某个文件（沙箱内）。"""
        att = self.get_attachment(session_id, file_id)
        if att is None or att.file_type != "archive":
            return "(附件不存在或非压缩包)"
        extract_dir = self._file_dir(session_id, file_id) / "extracted"
        p = _safe_join(extract_dir, rel_path)
        if p is None:
            return "(路径非法)"
        if not p.exists() or not p.is_file():
            return "(文件不存在)"
        try:
            raw = p.read_bytes()[:MAX_TEXT_READ]
            return raw.decode("utf-8", errors="replace") + ("\n...(截断)" if len(raw) >= MAX_TEXT_READ else "")
        except OSError as e:
            return f"(读取失败: {e})"

    def read_attachment_content(
        self, session_id: str, file_id: str
    ) -> str:
        """读取附件内容：代码/文本返回文本；图片返回说明；压缩包返回清单。"""
        att = self.get_attachment(session_id, file_id)
        if att is None:
            return "(附件不存在)"
        if att.file_type in ("code", "other", "document"):
            return self.read_text_content(session_id, file_id)
        if att.file_type == "image":
            return f"(图片附件 {att.filename}，无法以文本直接显示，如需理解请依赖视觉能力)"
        if att.file_type == "archive":
            r = self.extract_archive(session_id, file_id)
            if not r["ok"]:
                return f"(解压失败: {r.get('error')})"
            names = [e["name"] for e in r["entries"]]
            return "压缩包内容:\n" + "\n".join(names) if names else "(压缩包为空)"
        return "(未知类型)"


# ----------------------------------------------------------------------
# 附件工具（绑定到具体会话的 AttachmentStore）
# ----------------------------------------------------------------------

def attachment_tools(
    store: AttachmentStore,
    session_id: str,
    extra_description: str = "",
) -> list[AgentTool]:
    """为指定会话生成附件访问工具。

    这些工具让 Agent 能按需读取当前会话上传的附件，供 CTF/渗透/复测等场景使用。
    """
    desc = extra_description or "读取当前会话中用户上传的附件内容（代码/文本/文档）。"

    def _list_all() -> str:
        atts = store.list_attachments(session_id)
        if not atts:
            return "(当前会话无附件)"
        return "\n".join(f"- {a.filename} (id={a.file_id}, 类型={a.file_type})" for a in atts)

    def _read(file_id: str) -> str:
        return store.read_attachment_content(session_id, file_id)

    def _extract(file_id: str) -> str:
        r = store.extract_archive(session_id, file_id)
        if not r["ok"]:
            return f"(解压失败: {r.get('error')})"
        names = [e["name"] for e in r["entries"]]
        return "压缩包内容:\n" + ("\n".join(names) if names else "(压缩包为空)")

    def _read_extracted(file_id: str, path: str) -> str:
        return store.read_extracted_file(session_id, file_id, path)

    return [
        AgentTool(
            name="list_attachments",
            description="列出当前会话中用户上传的全部附件（id、文件名、类型）。",
            parameters={"type": "object", "properties": {}},
            handler=_list_all,
        ),
        AgentTool(
            name="read_attachment_file",
            description=desc,
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "附件 id（用 list_attachments 获取）"},
                },
                "required": ["file_id"],
            },
            handler=_read,
        ),
        AgentTool(
            name="extract_attachment_archive",
            description="解压压缩包附件并列出其中文件清单（支持 zip/tar/tar.gz）。",
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "压缩包附件 id"},
                },
                "required": ["file_id"],
            },
            handler=_extract,
        ),
        AgentTool(
            name="read_extracted_file",
            description="读取压缩包解压后目录中的某个文件内容（沙箱内路径）。",
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "压缩包附件 id"},
                    "path": {"type": "string", "description": "压缩包内的相对路径，如 'src/main.py'"},
                },
                "required": ["file_id", "path"],
            },
            handler=_read_extracted,
        ),
    ]

