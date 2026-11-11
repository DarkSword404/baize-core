"""Type registry and built-in ASMP V1 node/relation types."""
from __future__ import annotations

import re
from typing import Any

from .db import ASMPDatabase
from .models import TypeDefinition, now

_TYPE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _attr(description: str, value_type: str = "string", required: bool = False) -> dict[str, Any]:
    return {
        "type": value_type,
        "required": required,
        "description": description,
    }


BUILTIN_NODE_TYPES: list[TypeDefinition] = [
    TypeDefinition(
        type_id="host", display_name="主机", base_type="asset", status="builtin",
        description="主机、IP 或域名等运行环境",
        attributes={
            "address": _attr("IP 或域名"),
            "os": _attr("操作系统"),
            "services": _attr("暴露服务", "array"),
        },
        threat_profile=["exposure", "network_boundary", "service_inventory"],
    ),
    TypeDefinition(
        type_id="web_application", display_name="Web 应用", base_type="service", status="builtin",
        description="Web 页面、门户和业务应用",
        attributes={
            "url": _attr("入口 URL"),
            "tech_stack": _attr("技术栈", "array"),
            "authentication": _attr("认证方式"),
            "forms": _attr("表单", "array"),
        },
        threat_profile=["web", "authentication", "input_validation", "authorization"],
    ),
    TypeDefinition(
        type_id="api_service", display_name="API 服务", base_type="service", status="builtin",
        description="REST/GraphQL/RPC 等接口服务",
        attributes={
            "base_url": _attr("API 基础 URL"),
            "endpoints": _attr("端点", "array"),
            "auth_scheme": _attr("认证方式"),
            "spec": _attr("接口规范"),
        },
        threat_profile=["api", "authentication", "authorization", "object_access"],
    ),
    TypeDefinition(
        type_id="authentication_service", display_name="认证服务", base_type="service", status="builtin",
        description="登录、身份认证、会话和身份提供方",
        attributes={
            "protocol": _attr("认证协议"),
            "identity_provider": _attr("身份提供方", "reference"),
            "session_mechanism": _attr("会话机制"),
            "endpoints": _attr("认证端点", "array"),
            "fields": _attr("表单字段", "array"),
        },
        threat_profile=["authentication", "session_management", "identity_trust"],
    ),
    TypeDefinition(
        type_id="identity", display_name="身份主体", base_type="actor", status="builtin",
        description="用户、员工、访客、服务账号等主体",
        attributes={
            "identity_type": _attr("身份类型"),
            "permissions": _attr("权限", "array"),
            "trusted_by": _attr("被哪些服务信任", "array"),
        },
        threat_profile=["identity_trust", "privilege_boundary", "authorization"],
    ),
    TypeDefinition(
        type_id="database", display_name="数据库", base_type="data_store", status="builtin",
        description="关系型/文档型/缓存等数据存储",
        attributes={
            "engine": _attr("数据库类型"),
            "network_exposure": _attr("网络暴露"),
            "access_control": _attr("访问控制"),
            "data_sensitivity": _attr("数据敏感性"),
        },
        threat_profile=["data_exposure", "authorization", "network_boundary"],
    ),
    TypeDefinition(
        type_id="network_service", display_name="网络服务", base_type="service", status="builtin",
        description="端口、协议和网络服务",
        attributes={
            "port": _attr("端口", "integer"),
            "protocol": _attr("协议"),
            "product": _attr("产品/版本"),
            "tls": _attr("TLS 配置"),
        },
        threat_profile=["exposure", "service_version", "network_boundary"],
    ),
    TypeDefinition(
        type_id="file_storage", display_name="文件存储", base_type="data_store", status="builtin",
        description="对象存储、共享目录、上传目录等",
        attributes={
            "path": _attr("路径/桶"),
            "access_control": _attr("访问控制"),
            "sensitive_data": _attr("敏感数据"),
        },
        threat_profile=["data_exposure", "authorization"],
    ),
    TypeDefinition(
        type_id="cloud_resource", display_name="云资源", base_type="asset", status="builtin",
        description="云主机、容器、函数、托管服务等",
        attributes={
            "provider": _attr("云厂商"),
            "resource_type": _attr("资源类型"),
            "exposure": _attr("暴露面"),
        },
        threat_profile=["cloud_iam", "exposure", "trust_boundary"],
    ),
    TypeDefinition(
        type_id="security_control", display_name="安全控制", base_type="control", status="builtin",
        description="WAF、认证网关、访问控制、审计等控制措施",
        attributes={
            "control_type": _attr("控制类型"),
            "scope": _attr("适用范围"),
            "verified": _attr("是否已验证", "boolean"),
        },
        threat_profile=["control_effectiveness", "bypass"],
    ),
    TypeDefinition(
        type_id="data_asset", display_name="数据资产", base_type="data_store", status="builtin",
        description="有业务价值或敏感性的数据集合",
        attributes={
            "data_type": _attr("数据类型"),
            "sensitivity": _attr("敏感性"),
            "flow": _attr("流向", "array"),
        },
        threat_profile=["data_exposure", "privacy", "authorization"],
    ),
]

BUILTIN_RELATION_TYPES: list[TypeDefinition] = [
    TypeDefinition(type_id="connects_to", kind="relation", display_name="连接", status="builtin",
                   description="网络或协议层连接", threat_profile=["network_boundary"]),
    TypeDefinition(type_id="hosts", kind="relation", display_name="承载", status="builtin",
                   description="主机承载服务/应用", threat_profile=["exposure"]),
    TypeDefinition(type_id="authenticates_for", kind="relation", display_name="认证", status="builtin",
                   description="认证服务为应用/主体提供认证", threat_profile=["authentication"]),
    TypeDefinition(type_id="trusts", kind="relation", display_name="信任", status="builtin",
                   description="一个组件信任另一个组件签发的身份/数据", threat_profile=["identity_trust"]),
    TypeDefinition(type_id="can_access", kind="relation", display_name="可访问", status="builtin",
                   description="主体对客体具备某种访问权限（必须带条件）", threat_profile=["authorization"]),
    TypeDefinition(type_id="depends_on", kind="relation", display_name="依赖", status="builtin",
                   description="组件依赖", threat_profile=["supply_chain", "availability"]),
    TypeDefinition(type_id="transmits_data_to", kind="relation", display_name="数据传输", status="builtin",
                   description="数据流向", threat_profile=["data_flow"]),
    TypeDefinition(type_id="exposes", kind="relation", display_name="暴露", status="builtin",
                   description="组件暴露入口/资源", threat_profile=["exposure"]),
    TypeDefinition(type_id="derived_from", kind="relation", display_name="派生", status="builtin",
                   description="推断/派生关系", threat_profile=["evidence"]),
]

RELATION_TYPE_IDS = frozenset(t.type_id for t in BUILTIN_RELATION_TYPES)


def seed_types(db: ASMPDatabase) -> None:
    for definition in BUILTIN_NODE_TYPES + BUILTIN_RELATION_TYPES:
        if db.get_type(definition.type_id) is None:
            db.upsert_type(definition)


def validate_type_definition(definition: dict[str, Any],
                             existing: list[TypeDefinition] | None = None) -> list[str]:
    """Return validation errors. An empty list means structurally valid."""
    errors: list[str] = []
    type_id = str(definition.get("type_id") or "").strip()
    if not _TYPE_ID_RE.match(type_id):
        errors.append("type_id 必须是小写字母开头、3~64 位的 snake_case")
    kind = str(definition.get("kind") or "node")
    if kind not in ("node", "relation"):
        errors.append("kind 必须是 node 或 relation")
    if not str(definition.get("display_name") or "").strip():
        errors.append("display_name 不能为空")
    attributes = definition.get("attributes") or {}
    if not isinstance(attributes, dict):
        errors.append("attributes 必须是对象")
    else:
        for name, spec in attributes.items():
            if not re.match(r"^[a-z][a-z0-9_]{1,63}$", str(name)):
                errors.append(f"属性名不合法: {name}")
            if not isinstance(spec, dict) or not spec.get("type"):
                errors.append(f"属性缺少类型: {name}")
    if existing:
        for old in existing:
            if old.type_id == type_id:
                errors.append(f"类型已存在: {type_id}")
            if (old.kind == kind and old.display_name
                    and old.display_name == str(definition.get("display_name") or "")):
                errors.append(f"类型显示名重复: {old.display_name}")
    return errors


def propose_type(db: ASMPDatabase, definition: dict[str, Any],
                 reason: str = "") -> dict[str, Any]:
    existing = db.list_types()
    errors = validate_type_definition(definition, existing)
    if errors:
        return {"ok": False, "errors": errors, "type": None}
    t = TypeDefinition(
        type_id=str(definition["type_id"]).strip(),
        kind=str(definition.get("kind") or "node"),
        display_name=str(definition.get("display_name") or "").strip(),
        schema_version=int(definition.get("schema_version") or 1),
        base_type=str(definition.get("base_type") or ""),
        attributes=dict(definition.get("attributes") or {}),
        threat_profile=list(definition.get("threat_profile") or []),
        analysis_version=int(definition.get("analysis_version") or 1),
        status="proposed",
        description=str(definition.get("description") or ""),
        proposal_reason=reason[:1000],
        created_at=now(), updated_at=now(), version=1,
    )
    db.upsert_type(t)
    return {"ok": True, "errors": [], "type": t.to_dict()}


def register_type(db: ASMPDatabase, type_id: str) -> dict[str, Any]:
    t = db.get_type(type_id)
    if t is None:
        return {"ok": False, "error": "类型不存在"}
    if t.status == "builtin":
        return {"ok": True, "type": t.to_dict()}
    if t.status not in ("proposed", "validated"):
        return {"ok": False, "error": f"状态不允许注册: {t.status}"}
    t.status = "registered"
    t.updated_at = now()
    t.version += 1
    db.upsert_type(t)
    return {"ok": True, "type": t.to_dict()}
