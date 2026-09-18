"""任务归档管理。

任务结束（archive）时把 ``~/.baize/sessions/{id}.json`` 移动到
``~/.baize/archives/{id}.json``，并在 JSON 中标记 ``status="archived"``、
``archived_at``。任务记录页面列出归档，可"恢复为任务"反向移回
``sessions/``。

工作区与附件默认保留（供恢复后继续使用）；如需释放可在恢复前调
``delete_archive`` 永久删除。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from baize.config import DEFAULT_BAIZE_DIR

logger = logging.getLogger("baize.archives")

ARCHIVE_DIR = DEFAULT_BAIZE_DIR / "archives"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArchiveManager:
    """任务归档管理（线程安全）。"""

    def __init__(self, archive_dir: Path | None = None) -> None:
        self._dir = archive_dir or ARCHIVE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- 归档/恢复 ----------------------------------------------------
    def archive(self, session_json: Path) -> Path:
        """把 session JSON 文件移动到归档目录，标记 ``status=archived``。

        调用方应在移动前完成容器停止 + 内存移除，本方法仅处理文件层。
        """
        if not session_json.exists():
            raise FileNotFoundError(f"session json not found: {session_json}")
        with self._lock:
            data: dict[str, Any]
            try:
                data = json.loads(session_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # 损坏的 JSON 仍按原样归档，避免数据丢失
                data = {}
            data["status"] = "archived"
            data["archived_at"] = _now()
            # 清空容器绑定（容器已停止）
            data["container_id"] = None
            data["container_bound_at"] = None

            target = self._dir / session_json.name
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)
            session_json.unlink()
            logger.info("已归档任务 %s -> %s", session_json.stem, target)
            return target

    def restore(self, session_id: str, sessions_dir: Path) -> Path:
        """把归档移回 sessions 目录，重置 status=active。"""
        archive_file = self._dir / f"{session_id}.json"
        if not archive_file.exists():
            raise FileNotFoundError(f"archive not found: {archive_file}")
        with self._lock:
            data: dict[str, Any]
            try:
                data = json.loads(archive_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            data["status"] = "active"
            data["archived_at"] = None
            data["container_id"] = None
            data["container_bound_at"] = None

            target = sessions_dir / f"{session_id}.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)
            archive_file.unlink()
            logger.info("已恢复任务归档 %s -> %s", archive_file.stem, target)
            return target

    # ---- 查询 ----------------------------------------------------------
    def list_archives(self) -> list[dict]:
        with self._lock:
            out: list[dict] = []
            for f in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        out.append(data)
                except (json.JSONDecodeError, OSError):
                    continue
            return out

    def get_archive(self, session_id: str) -> dict | None:
        f = self._dir / f"{session_id}.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def delete_archive(self, session_id: str) -> bool:
        with self._lock:
            f = self._dir / f"{session_id}.json"
            if not f.exists():
                return False
            f.unlink()
            logger.info("已永久删除归档 %s", session_id)
            return True
