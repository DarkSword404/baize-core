"""Type-specific threat analyzers.

They generate candidate hypotheses and explicit unknowns. They never mark a
hypothesis confirmed; only evidence + execution can do that.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from .db import ASMPDatabase
from .models import Node, Relation, ThreatHypothesis, Unknown, now
from baize.memory.utils import new_id

logger = logging.getLogger("baize.plugins.asmp.analyzers")


def _key(node: Node, category: str, claim: str) -> str:
    # Key by semantic claim, not by node id: the same threat hypothesis across
    # several applications should become one object with multiple target_refs,
    # not N near-duplicate objects.
    digest = hashlib.sha1(claim.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"{node.engagement_id}:{category}:{digest}"


def _hypothesis(node: Node, *, category: str, title: str, claim: str,
                impact: str, confidence: float,
                preconditions: list[dict[str, Any]],
                evidence_refs: list[str] | None = None) -> ThreatHypothesis:
    return ThreatHypothesis(
        id=new_id("th"),
        engagement_id=node.engagement_id,
        title=title,
        category=category,
        target_refs=[node.id],
        claim=claim,
        supporting_evidence_refs=list(evidence_refs or node.evidence_refs),
        preconditions=preconditions,
        impact={"level": impact, "rationale": "需要结合后续验证确认实际影响"},
        confidence=confidence,
        status="candidate",
        created_at=now(), updated_at=now(), version=1,
        key=_key(node, category, claim),
    )


def _unknown(node: Node, attribute: str, reason: str,
             affected: list[str], suggested: list[str],
             priority: float = 0.6) -> Unknown:
    return Unknown(
        id=new_id("unk"),
        engagement_id=node.engagement_id,
        target_refs=[node.id],
        attribute=attribute,
        reason=reason,
        affects=affected,
        verifiable=True,
        suggested_evidence=suggested,
        priority=priority,
        status="open",
        created_at=now(), updated_at=now(),
        key=f"{node.engagement_id}:{attribute}",
    )


def _attr_state(node: Node, name: str) -> str:
    value = (node.attributes or {}).get(name)
    if isinstance(value, dict):
        return str(value.get("state") or "unknown")
    return "unknown" if value is None else "observed"


def analyze_node(db: ASMPDatabase, node: Node,
                 relations: list[Relation] | None = None) -> dict[str, Any]:
    relations = relations or []
    created_h: list[str] = []
    created_u: list[str] = []

    def add_h(h: ThreatHypothesis) -> None:
        saved, created = db.upsert_hypothesis(h)
        if created:
            created_h.append(saved.id)

    def add_u(u: Unknown) -> None:
        saved, created = db.upsert_unknown(u)
        if created:
            created_u.append(saved.id)

    if node.type_id == "authentication_service":
        if _attr_state(node, "session_mechanism") == "unknown":
            add_u(_unknown(
                node, "session_mechanism",
                "认证服务的会话建立、续期和失效机制尚未观察到",
                ["session_management", "identity_trust"],
                ["登录后的 Set-Cookie / Token", "会话过期行为", "登出后旧会话是否失效"],
                priority=0.8,
            ))
        if _attr_state(node, "identity_provider") == "unknown":
            add_u(_unknown(
                node, "identity_provider",
                "认证服务信任的身份来源尚未确认",
                ["identity_trust", "authorization"],
                ["OIDC/SAML 配置", "认证响应字段", "身份声明映射关系"],
                priority=0.75,
            ))
        add_h(_hypothesis(
            node, category="authentication",
            title="认证入口可能存在会话或认证绕过问题",
            claim="需要验证认证服务是否正确校验凭据、会话和身份声明",
            impact="high", confidence=0.42,
            preconditions=[
                {"description": "存在可用的测试身份或一次性测试账号", "state": "unknown"},
                {"description": "认证请求处于授权范围内", "state": "observed"},
            ],
            evidence_refs=node.evidence_refs,
        ))
        add_h(_hypothesis(
            node, category="session_management",
            title="会话失效或身份映射可能异常",
            claim="需要验证会话续期、失效和不同主体之间的身份映射边界",
            impact="medium", confidence=0.35,
            preconditions=[
                {"description": "能够观察登录与登出后的会话状态", "state": "unknown"},
            ],
        ))

    elif node.type_id in ("web_application", "api_service"):
        add_h(_hypothesis(
            node, category="authorization",
            title="应用可能存在对象级或功能级授权缺陷",
            claim="需要验证服务端是否对每个对象/操作执行授权，而不是只依赖前端隐藏",
            impact="high", confidence=0.4,
            preconditions=[
                {"description": "存在可区分的测试身份或权限角色", "state": "unknown"},
                {"description": "存在受权限约束的数据对象或功能", "state": "unknown"},
            ],
        ))
        add_h(_hypothesis(
            node, category="input_validation",
            title="输入处理路径可能存在未验证的注入或解析问题",
            claim="需要验证请求参数、表单和 API 输入在服务端的处理边界",
            impact="high", confidence=0.35,
            preconditions=[
                {"description": "已识别可提交的输入点", "state": "observed"},
            ],
        ))
        if _attr_state(node, "authentication") == "unknown":
            add_u(_unknown(
                node, "authentication",
                "应用实际认证方式与授权边界尚未确认",
                ["authorization", "authentication"],
                ["登录流程", "受保护页面", "未认证与已认证响应差异"],
                priority=0.8,
            ))
        scripts_value = (node.attributes or {}).get("scripts")
        scripts = scripts_value.get("value") if isinstance(scripts_value, dict) else scripts_value
        if isinstance(scripts, list) and scripts:
            add_u(_unknown(
                node, "javascript_assets",
                "页面引用的同源脚本尚未完整分析",
                ["web", "api_discovery", "authorization"],
                [f"读取并分析脚本: {s}" for s in scripts[:8]],
                priority=0.65,
            ))

    elif node.type_id == "database":
        add_h(_hypothesis(
            node, category="data_access",
            title="数据库访问控制可能超出最小权限",
            claim="需要验证应用访问数据库时使用的身份和实际权限范围",
            impact="high", confidence=0.35,
            preconditions=[
                {"description": "能够确认应用与数据库的连接关系", "state": "unknown"},
                {"description": "存在可验证的数据访问路径", "state": "unknown"},
            ],
        ))
        add_u(_unknown(
            node, "access_control",
            "数据库认证方式、授权范围与网络边界尚未确认",
            ["data_exposure", "authorization"],
            ["连接配置", "数据库账户权限", "网络访问控制"],
            priority=0.7,
        ))

    elif node.type_id in ("host", "network_service"):
        add_h(_hypothesis(
            node, category="exposure",
            title="暴露服务可能存在未验证的攻击面",
            claim="需要验证开放服务的版本、配置和访问边界",
            impact="medium", confidence=0.3,
            preconditions=[
                {"description": "服务在授权范围内可达", "state": "observed"},
            ],
        ))

    else:
        add_h(_hypothesis(
            node, category="generic",
            title=f"{node.type_id} 的关键属性或边界尚未验证",
            claim="需要补充该节点的关键属性和关系证据",
            impact="medium", confidence=0.3,
            preconditions=[
                {"description": "节点处于授权范围内", "state": "observed"},
            ],
        ))

    db.record_analysis_run(
        node.engagement_id, analyzer=f"type:{node.type_id}",
        target_refs=[node.id], status="completed",
        output={"hypotheses_created": created_h, "unknowns_created": created_u},
    )
    return {"node_id": node.id, "hypotheses_created": created_h,
            "unknowns_created": created_u}


def analyze_model_delta(db: ASMPDatabase, engagement_id: str,
                        node_ids: list[str]) -> dict[str, Any]:
    out = []
    for nid in node_ids:
        node = db.get_node(nid)
        if node is None:
            continue
        out.append(analyze_node(db, node, db.list_relations(engagement_id)))
    return {"analyzed": len(out), "results": out}
