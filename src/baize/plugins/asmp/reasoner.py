"""Relation gaps and bounded path hypotheses.

This is intentionally conservative. A path is a hypothesis, not an exploit and
not proof of access. It lists the conditions and evidence still missing.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .db import ASMPDatabase
from .models import Node, Relation, ThreatHypothesis, Unknown, now
from baize.memory.utils import new_id

_PATH_EDGE_TYPES = {
    "connects_to", "hosts", "authenticates_for", "trusts",
    "can_access", "depends_on", "transmits_data_to", "exposes",
}
_PATH_NODE_TYPES = {
    "identity", "authentication_service", "web_application",
    "api_service", "database", "data_asset", "network_service",
}


def _hkey(engagement_id: str, category: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"{engagement_id}:{category}:{digest}"


def relation_gaps(db: ASMPDatabase, engagement_id: str) -> list[Unknown]:
    nodes = db.list_nodes(engagement_id)
    relations = db.list_relations(engagement_id)
    by_id = {n.id: n for n in nodes}
    gaps: list[Unknown] = []

    def has_rel(source_id: str, target_id: str, types: set[str]) -> bool:
        return any(r.source_id == source_id and r.target_id == target_id
                   and r.type_id in types for r in relations)

    auth_nodes = [n for n in nodes if n.type_id == "authentication_service"]
    identity_nodes = [n for n in nodes if n.type_id == "identity"]
    web_nodes = [n for n in nodes if n.type_id in ("web_application", "api_service")]
    for auth in auth_nodes:
        if not any(has_rel(i.id, auth.id, {"authenticates_for", "trusts"})
                   for i in identity_nodes):
            gaps.append(Unknown(
                id=new_id("unk"), engagement_id=engagement_id,
                target_refs=[auth.id], attribute="identity_trust",
                reason="认证服务已发现，但身份主体/信任关系尚未建模",
                affects=["identity_trust", "authorization"],
                suggested_evidence=["可区分的测试身份", "认证后的身份声明", "不同主体的权限差异"],
                priority=0.8, status="open", created_at=now(), updated_at=now(),
                key=f"{engagement_id}:{auth.id}:identity_trust",
            ))
    for web in web_nodes:
        if not any(has_rel(web.id, d.id, {"can_access", "depends_on", "transmits_data_to"})
                   for d in nodes if d.type_id in ("database", "data_asset")):
            gaps.append(Unknown(
                id=new_id("unk"), engagement_id=engagement_id,
                target_refs=[web.id], attribute="data_access_relation",
                reason="应用与数据资产之间的访问关系尚未确认",
                affects=["data_exposure", "authorization"],
                suggested_evidence=["应用配置", "后端数据访问路径", "最小权限验证"],
                priority=0.55, status="open", created_at=now(), updated_at=now(),
                key=f"{engagement_id}:{web.id}:data_access_relation",
            ))
    for gap in gaps:
        db.upsert_unknown(gap)
    return gaps


def infer_paths(db: ASMPDatabase, engagement_id: str, max_depth: int = 3,
                limit: int = 20) -> list[ThreatHypothesis]:
    nodes = db.list_nodes(engagement_id)
    relations = db.list_relations(engagement_id)
    by_id = {n.id: n for n in nodes}
    adjacency: dict[str, list[tuple[str, Relation]]] = {}
    for rel in relations:
        if rel.type_id not in _PATH_EDGE_TYPES or rel.status in ("disproven", "stale"):
            continue
        adjacency.setdefault(rel.source_id, []).append((rel.target_id, rel))
        if rel.direction != "directed":
            adjacency.setdefault(rel.target_id, []).append((rel.source_id, rel))

    created: list[ThreatHypothesis] = []
    seen: set[tuple[str, ...]] = set()
    identity_nodes = [n for n in nodes if n.type_id == "identity"]

    def walk(current: str, path: list[str], edges: list[Relation], depth: int) -> None:
        if len(created) >= limit or depth > max_depth:
            return
        node = by_id.get(current)
        if node is None:
            return
        if depth > 0 and node.type_id in ("database", "data_asset"):
            key = tuple(path)
            if key not in seen:
                seen.add(key)
                names = [by_id[i].name for i in path if i in by_id]
                claim = (
                    "存在一条潜在关系链：" + " → ".join(names) +
                    "。链路只表示关系组合，不代表已获得访问权限或可利用。"
                )
                h = ThreatHypothesis(
                    id=new_id("th"), engagement_id=engagement_id,
                    title="潜在跨组件访问路径需要条件验证",
                    category="path",
                    target_refs=list(path),
                    claim=claim,
                    supporting_evidence_refs=[
                        ev for e in edges for ev in (e.evidence_refs or [])
                    ][:20],
                    preconditions=[
                        {"description": f"关系 {e.type_id} 的证据条件需要逐条确认", "state": "unknown"}
                        for e in edges
                    ],
                    impact={"level": "high", "rationale": "跨身份/数据边界可能扩大影响"},
                    confidence=min(0.6, 0.2 + 0.1 * len(edges)),
                    status="candidate", created_at=now(), updated_at=now(),
                    version=1, key=_hkey(engagement_id, "path", claim),
                )
                saved, was_created = db.upsert_hypothesis(h)
                if was_created:
                    created.append(saved)
            return
        if depth >= max_depth:
            return
        for target, rel in adjacency.get(current, []):
            if target in path or target not in by_id:
                continue
            if by_id[target].type_id not in _PATH_NODE_TYPES:
                continue
            walk(target, path + [target], edges + [rel], depth + 1)

    for identity in identity_nodes:
        walk(identity.id, [identity.id], [], 0)

    db.record_analysis_run(
        engagement_id, analyzer="path:limited", target_refs=[n.id for n in nodes],
        status="completed", output={"paths_created": [h.id for h in created]},
    )
    return created
