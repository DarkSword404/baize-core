"""报告管理 API。

端点：
    GET    /api/v1/reports/templates             内置模板列表
    GET    /api/v1/reports/templates/{tpl_id}    模板详情（含章节）
    GET    /api/v1/reports                       报告列表（可选 session_id 过滤）
    POST   /api/v1/reports                       直接创建完整报告
    GET    /api/v1/reports/{report_id}           报告详情（元数据 + Markdown 正文）
    GET    /api/v1/reports/{report_id}/download  下载 .md 文件
    DELETE /api/v1/reports/{report_id}           删除报告
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.responses import Response

try:
    from pydantic import BaseModel
except ImportError:  # pragma: no cover
    BaseModel = object  # type: ignore

from baize.reports.templates import get_builtin_templates, get_template_by_id
from baize.reports.store import get_report_store

logger = logging.getLogger("baize.reports.api")


class CreateReportRequest(BaseModel if BaseModel is not object else object):
    title: str
    content: str = ""
    template_id: str = ""
    session_id: str = ""


def _require_api_key(request: Request) -> None:
    """与 baize-core 一致的 Token 校验。"""
    if not getattr(request.app.state, "require_auth", False):
        return
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if auth_manager is None:
        return
    key = request.headers.get("X-Baize-API-Key") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")
    if not auth_manager.validate_token(key):
        raise HTTPException(status_code=401, detail="无效的 API 密钥")


def register(app: FastAPI) -> None:
    """注册报告管理 API 路由。"""

    store = get_report_store()

    # ── 模板 ──

    @app.get("/api/v1/reports/templates",
             dependencies=[Depends(_require_api_key)])
    def list_templates() -> dict:
        return {
            "templates": [t.to_dict() for t in get_builtin_templates()],
        }

    @app.get("/api/v1/reports/templates/{template_id}",
             dependencies=[Depends(_require_api_key)])
    def get_template(template_id: str) -> dict:
        tpl = get_template_by_id(template_id)
        if tpl is None:
            raise HTTPException(status_code=404, detail="模板不存在")
        data = tpl.to_dict()
        data["skeleton"] = tpl.skeleton()
        return data

    # ── 报告管理 ──

    @app.get("/api/v1/reports",
             dependencies=[Depends(_require_api_key)])
    def list_reports(
        session_id: str = Query("", description="按会话过滤"),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict:
        records = store.list_reports(session_id=session_id, limit=limit)
        return {
            "reports": [r.to_dict() for r in records],
            "total": len(records),
        }

    @app.post("/api/v1/reports",
              dependencies=[Depends(_require_api_key)])
    def create_report(payload: CreateReportRequest) -> dict:
        tpl = get_template_by_id(payload.template_id) if payload.template_id else None
        rec = store.create_report(
            title=payload.title,
            content=payload.content,
            template_id=tpl.id if tpl else payload.template_id,
            template_name=tpl.name if tpl else "",
            session_id=payload.session_id,
        )
        return rec.to_dict()

    @app.get("/api/v1/reports/{report_id}",
             dependencies=[Depends(_require_api_key)])
    def get_report(report_id: str) -> dict:
        rec = store.get(report_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="报告不存在")
        content = store.get_content(report_id) or ""
        data = rec.to_dict()
        data["content"] = content
        return data

    @app.get("/api/v1/reports/{report_id}/download",
             dependencies=[Depends(_require_api_key)])
    def download_report(report_id: str) -> Response:
        rec = store.get(report_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="报告不存在")
        content = store.get_content(report_id) or ""
        # 文件名安全化（中文走 RFC 5987 编码）
        safe_title = rec.title.replace("/", "_").replace("\\", "_")[:100]
        filename = f"{safe_title or report_id}.md"
        encoded = urllib.parse.quote(filename)
        headers = {
            "Content-Disposition":
                f"attachment; filename*=UTF-8''{encoded}",
        }
        return Response(
            content=content.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers=headers,
        )

    @app.delete("/api/v1/reports/{report_id}",
                dependencies=[Depends(_require_api_key)])
    def delete_report(report_id: str) -> dict:
        if not store.delete_report(report_id):
            raise HTTPException(status_code=404, detail="报告不存在")
        return {"deleted": report_id}

    logger.info("Reports API 已注册")
