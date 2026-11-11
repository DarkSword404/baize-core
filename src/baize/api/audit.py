"""审计日志查询 / 导出 / 完整性校验 API。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response


def _parse_time(value: str) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"时间格式无效: {value!r}（支持 ISO 8601 或 Unix 时间戳）",
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _filters(
    action: str,
    actor: str,
    resource_type: str,
    resource_id: str,
    session_id: str,
    status: str,
    since: str,
    until: str,
    search: str,
) -> dict[str, Any]:
    return {
        "action": action,
        "actor": actor,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "session_id": session_id,
        "status": status,
        "since": _parse_time(since),
        "until": _parse_time(until),
        "search": search,
    }


def register(app, require_api_key: Callable[..., Any]) -> None:
    """注册审计 API。require_api_key 由主应用注入，避免循环导入。"""

    @app.get("/api/v1/audit", dependencies=[Depends(require_api_key)])
    def list_audit(
        request: Request,
        action: str = "",
        actor: str = "",
        resource_type: str = "",
        resource_id: str = "",
        session_id: str = "",
        status: str = "",
        since: str = "",
        until: str = "",
        search: str = "",
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        audit = request.app.state.audit
        return audit.query(
            **_filters(
                action, actor, resource_type, resource_id, session_id,
                status, since, until, search,
            ),
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/v1/audit/export",
        dependencies=[Depends(require_api_key)],
        response_class=Response,
    )
    def export_audit(
        request: Request,
        format: str = Query("jsonl", pattern="^(jsonl|csv)$"),
        action: str = "",
        actor: str = "",
        resource_type: str = "",
        resource_id: str = "",
        session_id: str = "",
        status: str = "",
        since: str = "",
        until: str = "",
        search: str = "",
    ) -> Response:
        audit = request.app.state.audit
        content, media_type, filename = audit.export(
            fmt=format,
            **_filters(
                action, actor, resource_type, resource_id, session_id,
                status, since, until, search,
            ),
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/v1/audit/verify", dependencies=[Depends(require_api_key)])
    def verify_audit(
        request: Request, limit: int = Query(0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return request.app.state.audit.verify(limit=limit)

    @app.get("/api/v1/audit/stats", dependencies=[Depends(require_api_key)])
    def audit_stats(request: Request) -> dict[str, Any]:
        return request.app.state.audit.stats()
