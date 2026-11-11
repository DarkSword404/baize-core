"""Exploration decision: hypotheses/unknowns -> prioritized task suggestions.

Tasks are proposals only. This module never calls a tool.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from .db import ASMPDatabase
from .models import ExplorationTask, ThreatHypothesis, now
from baize.memory.utils import new_id

_IMPACT_SCORE = {"critical": 1.0, "high": 0.8, "medium": 0.55, "low": 0.3}


def _task_key(kind: str, object_id: str) -> str:
    return f"task:{kind}:{object_id}"


def _score(*, impact: float, probability: float, uncertainty: float,
           coverage: float, cost: float) -> float:
    value = (
        0.45 * impact
        + 0.25 * max(0.0, min(1.0, probability))
        + 0.20 * max(0.0, min(1.0, uncertainty))
        + 0.10 * max(0.0, min(1.0, coverage))
        - 0.15 * max(0.0, min(1.0, cost / 10.0))
    )
    return round(max(0.0, min(1.0, value)), 4)


def task_for_hypothesis(h: ThreatHypothesis) -> ExplorationTask:
    impact = _IMPACT_SCORE.get(str((h.impact or {}).get("level") or "medium"), 0.55)
    unknown_pre = sum(
        1 for p in h.preconditions if str(p.get("state") or "") == "unknown"
    )
    uncertainty = min(1.0, 0.3 + 0.2 * unknown_pre)
    return ExplorationTask(
        id=new_id("task"),
        engagement_id=h.engagement_id,
        objective=f"验证假设：{h.title}",
        target_refs=list(h.target_refs),
        hypothesis_refs=[h.id],
        required_evidence=[
            "目标对象的请求/响应或配置证据",
            "能够区分假设成立与否的对照结果",
        ],
        preconditions=[
            str(p.get("description") or "") for p in h.preconditions
            if str(p.get("description") or "")
        ],
        risk_level="low" if impact < 0.7 else "medium",
        estimated_cost=2,
        priority=_score(impact=impact, probability=h.confidence,
                        uncertainty=uncertainty, coverage=0.25, cost=2),
        status="proposed",
        stop_conditions=["超出授权范围", "触发预设安全停止条件", "需要真实用户数据时停止"],
        created_at=now(), updated_at=now(),
        key=_task_key("hypothesis", h.id),
    )


def task_for_unknown(engagement_id: str, unknown: Any) -> ExplorationTask:
    priority = float(getattr(unknown, "priority", 0.5) or 0.5)
    return ExplorationTask(
        id=new_id("task"),
        engagement_id=engagement_id,
        objective=f"收集未知项：{getattr(unknown, 'attribute', '')}",
        target_refs=list(getattr(unknown, "target_refs", []) or []),
        required_evidence=list(getattr(unknown, "suggested_evidence", []) or []),
        preconditions=["目标处于授权范围内", "只采集完成任务所需的最小证据"],
        risk_level="low",
        estimated_cost=1,
        priority=_score(impact=0.55, probability=0.4, uncertainty=1.0,
                        coverage=0.5, cost=1),
        status="proposed",
        stop_conditions=["超出授权范围", "触发预设安全停止条件"],
        created_at=now(), updated_at=now(),
        key=_task_key("unknown", getattr(unknown, "id", "")),
    )


def _group_key(kind: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"task:group:{kind}:{digest}"


def _merge_tasks(tasks: list[ExplorationTask], kind: str, title: str) -> ExplorationTask:
    first = tasks[0]
    return ExplorationTask(
        id=new_id("task"),
        engagement_id=first.engagement_id,
        objective=title,
        target_refs=list(dict.fromkeys(x for t in tasks for x in t.target_refs)),
        hypothesis_refs=list(dict.fromkeys(x for t in tasks for x in t.hypothesis_refs)),
        required_evidence=list(dict.fromkeys(x for t in tasks for x in t.required_evidence)),
        preconditions=list(dict.fromkeys(x for t in tasks for x in t.preconditions)),
        risk_level=max((t.risk_level for t in tasks), key=lambda x: {"low": 0, "medium": 1, "high": 2}.get(x, 0)),
        estimated_cost=max(t.estimated_cost for t in tasks),
        priority=max(t.priority for t in tasks),
        status="proposed",
        stop_conditions=list(dict.fromkeys(x for t in tasks for x in t.stop_conditions)),
        created_at=now(), updated_at=now(),
        key=_group_key(kind, title),
    )


def propose_tasks(db: ASMPDatabase, engagement_id: str, limit: int = 10) -> list[ExplorationTask]:
    existing = {t.key: t for t in db.list_tasks(engagement_id)}
    raw: list[tuple[str, str, ExplorationTask]] = []
    for h in db.list_hypotheses(engagement_id):
        if h.status not in ("candidate", "ready", "in_progress", "blocked", "inconclusive"):
            continue
        task = existing.get(_task_key("hypothesis", h.id)) or task_for_hypothesis(h)
        if task.status in ("completed", "cancelled", "rejected"):
            continue
        raw.append(("hypothesis", h.title, task))
    for unknown in db.list_unknowns(engagement_id):
        if getattr(unknown, "status", "open") != "open":
            continue
        task = existing.get(_task_key("unknown", unknown.id)) or task_for_unknown(engagement_id, unknown)
        if task.status in ("completed", "cancelled", "rejected"):
            continue
        raw.append(("unknown", f"收集未知项：{getattr(unknown, 'attribute', '')}", task))

    # 同标题/同属性的假设/未知项合并为一个任务，target_refs 保留全部目标，
    # 避免 UI/调度器看到十条完全相同的 objective。
    grouped: dict[tuple[str, str], list[ExplorationTask]] = {}
    for kind, title, task in raw:
        grouped.setdefault((kind, title), []).append(task)
    candidates = [
        (existing.get(_group_key(kind, title)) or _merge_tasks(tasks, kind, title))
        for (kind, title), tasks in grouped.items()
    ]
    for task in candidates:
        if task.status in ("completed", "cancelled", "rejected"):
            continue

    candidates.sort(key=lambda t: t.priority, reverse=True)
    out: list[ExplorationTask] = []
    seen: set[str] = set()
    for task in candidates:
        if task.key in seen:
            continue
        seen.add(task.key)
        saved, _ = db.upsert_task(task)
        out.append(saved)
        if len(out) >= max(1, min(int(limit or 10), 50)):
            break
    db.record_analysis_run(
        engagement_id, analyzer="planner:propose_tasks",
        target_refs=[t.id for t in out], status="completed",
        output={"tasks": [t.id for t in out]},
    )
    return out


def apply_execution_result(db: ASMPDatabase, task_id: str, *,
                           status: str = "completed",
                           note: str = "") -> Optional[ExplorationTask]:
    task = db.get_task(task_id)
    if task is None:
        return None
    if status not in ("running", "completed", "failed", "cancelled", "rejected"):
        status = "completed"
    task.status = status
    task.updated_at = now()
    saved, _ = db.upsert_task(task)
    db.record_change(
        task.engagement_id, operation="task_result", object_type="task",
        object_id=task.id, before=None, after={"status": status, "note": note},
        reason=note or "execution result", actor="harness",
    )
    return saved
