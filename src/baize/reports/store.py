"""报告持久化存储。

存储布局（$BAIZE_DATA_DIR/reports/）：
- index.json       报告元数据列表（原子写入）
- {report_id}.md   报告正文（分段追加，最终可直接下载）

支持 draft → done 生命周期：agent 通过 start/append/finish 三段式
分段写入，避免超长内容一次性传输失败。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from baize.config import DEFAULT_BAIZE_DIR

logger = logging.getLogger("baize.reports.store")

REPORTS_DIR = DEFAULT_BAIZE_DIR / "reports"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReportRecord:
    """报告元数据。"""

    id: str
    title: str
    template_id: str = ""
    template_name: str = ""
    session_id: str = ""
    status: str = "done"  # draft | done
    created_at: str = ""
    updated_at: str = ""
    size: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ReportRecord":
        return cls(
            id=data["id"],
            title=data.get("title", "未命名报告"),
            template_id=data.get("template_id", ""),
            template_name=data.get("template_name", ""),
            session_id=data.get("session_id", ""),
            status=data.get("status", "done"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            size=int(data.get("size", 0)),
        )


class ReportStore:
    """报告文件存储（线程安全，进程内单例）。"""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or REPORTS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._records: dict[str, ReportRecord] = {}
        self._load_all()

    # ── 持久化 ──

    def _index_path(self) -> Path:
        return self._dir / "index.json"

    def _md_path(self, report_id: str) -> Path:
        return self._dir / f"{report_id}.md"

    def _load_all(self) -> None:
        idx = self._index_path()
        if not idx.exists():
            return
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            for item in data.get("reports", []):
                rec = ReportRecord.from_dict(item)
                # 仅加载正文仍存在的报告
                if self._md_path(rec.id).exists():
                    self._records[rec.id] = rec
        except (json.JSONDecodeError, OSError, KeyError):
            logger.warning("报告索引加载失败，忽略损坏数据", exc_info=True)

    def _save_index(self) -> None:
        payload = {
            "reports": [r.to_dict() for r in
                        sorted(self._records.values(),
                               key=lambda x: x.created_at, reverse=True)]
        }
        tmp = self._index_path().with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self._index_path())

    def _refresh_size(self, report_id: str) -> None:
        rec = self._records.get(report_id)
        if rec is None:
            return
        try:
            rec.size = self._md_path(report_id).stat().st_size
        except OSError:
            rec.size = 0

    # ── 查询 ──

    def list_reports(self, session_id: str = "",
                     limit: int = 100) -> list[ReportRecord]:
        with self._lock:
            items = list(self._records.values())
        if session_id:
            items = [r for r in items if r.session_id == session_id]
        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[:limit]

    def get(self, report_id: str) -> Optional[ReportRecord]:
        with self._lock:
            return self._records.get(report_id)

    def get_content(self, report_id: str) -> Optional[str]:
        with self._lock:
            if report_id not in self._records:
                return None
        path = self._md_path(report_id)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # ── 写入 ──

    def create_report(
        self,
        title: str,
        content: str = "",
        template_id: str = "",
        template_name: str = "",
        session_id: str = "",
        status: str = "done",
    ) -> ReportRecord:
        """一次性创建完整报告（服务端落盘，无单次长度限制）。"""
        report_id = f"rpt_{uuid.uuid4().hex[:16]}"
        now = _now()
        rec = ReportRecord(
            id=report_id,
            title=title.strip()[:200] or "未命名报告",
            template_id=template_id,
            template_name=template_name,
            session_id=session_id,
            status=status,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._md_path(report_id).write_text(content, encoding="utf-8")
            rec.size = len(content.encode("utf-8"))
            self._records[report_id] = rec
            self._save_index()
        logger.info("report created: %s (%s, %d bytes)",
                    report_id, rec.title, rec.size)
        return rec

    def start_report(
        self,
        title: str,
        skeleton: str = "",
        template_id: str = "",
        template_name: str = "",
        session_id: str = "",
    ) -> ReportRecord:
        """创建 draft 报告（分段写入起点）。"""
        return self.create_report(
            title=title,
            content=skeleton,
            template_id=template_id,
            template_name=template_name,
            session_id=session_id,
            status="draft",
        )

    def append_section(self, report_id: str, section_markdown: str) -> bool:
        """向 draft 报告追加一个章节（分段写入）。"""
        with self._lock:
            rec = self._records.get(report_id)
            if rec is None:
                return False
            path = self._md_path(report_id)
            chunk = section_markdown.strip()
            if not chunk:
                return True
            with open(path, "a", encoding="utf-8") as f:
                f.write(("\n\n" if path.stat().st_size > 0 else "") + chunk)
                f.write("\n")
            rec.updated_at = _now()
            self._refresh_size(report_id)
            self._save_index()
        return True

    def finish_report(self, report_id: str) -> Optional[ReportRecord]:
        """标记 draft 报告为完成。"""
        with self._lock:
            rec = self._records.get(report_id)
            if rec is None:
                return None
            rec.status = "done"
            rec.updated_at = _now()
            self._refresh_size(report_id)
            self._save_index()
            return rec

    def delete_report(self, report_id: str) -> bool:
        with self._lock:
            if report_id not in self._records:
                return False
            del self._records[report_id]
            path = self._md_path(report_id)
            if path.exists():
                path.unlink()
            self._save_index()
        return True


# ── 进程内单例 ──

_singleton: Optional[ReportStore] = None
_singleton_lock = threading.Lock()


def get_report_store() -> ReportStore:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ReportStore()
    return _singleton
