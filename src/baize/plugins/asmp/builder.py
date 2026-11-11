"""Dynamic model builder: observation -> nodes / relations / evidence / changes."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from .db import ASMPDatabase
from .models import Evidence, Node, Relation, now
from .normalizer import Observation, host_from_url, normalize_url

logger = logging.getLogger("baize.plugins.asmp.builder")


def observed(value: Any) -> dict[str, Any]:
    return {"value": value, "state": "observed"}


def unknown() -> dict[str, Any]:
    return {"value": None, "state": "unknown"}


class ModelBuilder:
    def __init__(self, db: ASMPDatabase) -> None:
        self.db = db

    # ---- public --------------------------------------------------------
    def ingest(self, obs: Observation) -> dict[str, Any]:
        changes = {
            "observation_id": obs.observation_id,
            "nodes_created": [],
            "nodes_updated": [],
            "relations_created": [],
            "relations_updated": [],
            "evidence": [],
            "warnings": [],
        }
        evidence = Evidence(
            id=f"ev_{obs.observation_id.removeprefix('obs_')}",
            engagement_id=obs.engagement_id,
            source_kind=obs.source_kind,
            source_ref=obs.source_ref,
            observation=self._summary(obs),
            raw_artifact_ref=obs.raw_artifact_ref,
            observed_at=obs.observed_at,
            scope_ref=obs.scope_ref,
            reliability="direct_observation" if obs.source_kind != "inferred" else "inferred",
            status="accepted",
        )
        self.db.add_evidence(evidence)
        changes["evidence"].append(evidence.id)

        data = obs.data or {}
        kind = obs.content_kind
        if kind == "tool_execution":
            self._ingest_tool(obs, evidence, data, changes)
        elif kind == "human_input":
            # Human input is evidence/hint, not an observed environment fact.
            pass
        elif kind in ("service_discovery", "web_observation", "generic"):
            self._ingest_structured(obs, evidence, data, changes)
        else:
            self._ingest_structured(obs, evidence, data, changes)

        for key in ("nodes_created", "nodes_updated", "relations_created", "relations_updated"):
            changes[key] = list(dict.fromkeys(changes[key]))
        return changes

    # ---- tool-specific -------------------------------------------------
    def _ingest_tool(self, obs: Observation, evidence: Evidence,
                     data: dict[str, Any], changes: dict[str, Any]) -> None:
        tool = str(data.get("tool") or "")
        args = data.get("arguments") or {}
        output = str(data.get("output") or "")
        target = str(args.get("url") or args.get("target") or args.get("host") or
                     args.get("domain") or "")
        artifacts = data.get("artifacts") or {}
        host = host_from_url(target) or str(args.get("host") or args.get("domain") or "")
        if host and not re.search(r"\s", host):
            host_node = self._upsert_node(
                obs, evidence, "host", host,
                summary="目标主机/域名", attributes={"address": observed(host)},
                changes=changes,
            )
        else:
            host_node = None

        if target and tool in (
            "http_request", "http_session_request", "http_probe", "whatweb_fingerprint",
            "nikto_scan", "waf_detect", "nuclei_scan", "ffuf_fuzz",
        ):
            self._ingest_web(obs, evidence, target, artifacts, host_node, changes)

        # common port scan outputs: "开放端口: 22, 80" / "22/tcp open"
        ports = set()
        for m in re.finditer(r"(?<![\d])(\d{1,5})(?:/tcp)?\s*(?:open|开放)", output, flags=re.I):
            ports.add(int(m.group(1)))
        m = re.search(r"开放端口[:：]\s*([0-9,\s]+)", output)
        if m:
            for p in re.findall(r"\d+", m.group(1)):
                ports.add(int(p))
        if tool in ("port_scan", "nmap_scan", "masscan_scan"):
            for port in sorted(p for p in ports if 1 <= p <= 65535):
                service = self._upsert_node(
                    obs, evidence, "network_service", f"{host}:{port}" if host else f"port:{port}",
                    summary=f"{host or 'target'} 端口 {port}",
                    attributes={"port": observed(port), "protocol": observed("tcp")},
                    changes=changes,
                )
                if host_node is not None:
                    self._upsert_relation(
                        obs, evidence, host_node.id, service.id, "hosts",
                        confidence=1.0, changes=changes,
                    )

        # DNS result often is a bare IP; keep it as a host attribute.
        if tool == "dns_lookup" and host_node is not None:
            ip = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", output)
            if ip:
                host_node.attributes["address"] = observed(host)
                host_node.attributes["ip"] = observed(ip.group(1))
                self.db.upsert_node(host_node)
                self.db.record_change(
                    obs.engagement_id, operation="update", object_type="node",
                    object_id=host_node.id, before=None, after=host_node.to_dict(),
                    reason="DNS observation", actor="plugin",
                )

        # WAF/Nikto are controls/observations, not findings.
        if tool in ("waf_detect", "nikto_scan") and host_node is not None:
            control = self._upsert_node(
                obs, evidence, "security_control", f"{host}:{tool}",
                summary=f"{host} 的 {tool} 观察",
                attributes={
                    "control_type": observed(tool),
                    "scope": observed(target or host),
                    "verified": observed(True),
                },
                changes=changes,
            )
            self._upsert_relation(
                obs, evidence, host_node.id, control.id, "exposes",
                confidence=0.7, changes=changes,
            )

    @staticmethod
    def _is_static_asset(url: str) -> bool:
        path = urlparse(url).path.lower()
        if path.endswith((
            ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".ico", ".woff", ".woff2", ".ttf", ".map", ".xml", ".txt",
        )):
            return True
        return path in ("/robots.txt", "/sitemap.xml", "/favicon.ico")

    def _ingest_web(self, obs: Observation, evidence: Evidence, target: str,
                    artifacts: dict[str, Any], host_node: Optional[Node],
                    changes: dict[str, Any]) -> None:
        url = normalize_url(target)
        if self._is_static_asset(url):
            # 静态资源不是独立应用节点；保留为证据/主机上下文，避免模型被
            # robots.txt、CSS、favicon 等低价值 URL 淹没。
            return
        host = host_from_url(url)
        path = urlparse(url).path.lower()
        is_auth = "/auth/" in path or path.endswith("/login") or "/login/" in path
        is_api = "/api/" in url or url.rstrip("/").endswith("/api")
        if is_auth:
            app_type = "authentication_service"
        else:
            app_type = "api_service" if is_api else "web_application"
        attrs: dict[str, Any] = {}
        if app_type == "web_application":
            attrs["url"] = observed(url)
        elif app_type == "authentication_service":
            attrs["endpoints"] = observed([url])
        else:
            attrs["base_url"] = observed(url)
        scripts = list(artifacts.get("scripts") or [])
        if scripts:
            attrs["scripts"] = observed(scripts)
        app = self._upsert_node(
            obs, evidence, app_type, url,
            summary=f"{'认证服务' if is_auth else ('API 服务' if is_api else 'Web 应用')} {url}",
            attributes=attrs,
            changes=changes,
        )
        if host_node is not None:
            self._upsert_relation(
                obs, evidence, host_node.id, app.id, "hosts",
                confidence=1.0, changes=changes,
            )

        forms = list(artifacts.get("forms") or [])
        apis = list(artifacts.get("apis") or [])
        if forms:
            app.attributes.setdefault("forms", observed(forms))
            self.db.upsert_node(app)
        for form in forms:
            method, _, rest = form.partition(" ")
            action = rest.split(" fields=", 1)[0].strip()
            fields = []
            if " fields=" in rest:
                raw_fields = rest.split(" fields=", 1)[1].strip()[1:-1]
                fields = [x.strip() for x in raw_fields.split(",") if x.strip()]
            action_url = urljoin(url, action) if action.startswith("/") else action
            auth = self._upsert_node(
                obs, evidence, "authentication_service", action_url,
                summary=f"认证入口 {action_url}",
                attributes={
                    "endpoints": observed([action_url]),
                    "fields": observed(fields),
                    "session_mechanism": unknown(),
                    "identity_provider": unknown(),
                },
                changes=changes,
            )
            self._upsert_relation(
                obs, evidence, app.id, auth.id, "exposes",
                conditions=[{"description": "认证入口由该应用暴露", "state": "observed"}],
                confidence=0.85, changes=changes,
            )
        for api in apis:
            api_url = urljoin(url, api) if api.startswith("/") else api
            api_node = self._upsert_node(
                obs, evidence, "api_service", api_url,
                summary=f"API 端点 {api_url}",
                attributes={"base_url": observed(api_url), "endpoints": observed([api_url])},
                changes=changes,
            )
            self._upsert_relation(
                obs, evidence, app.id, api_node.id, "exposes",
                confidence=0.7, changes=changes,
            )

    def _ingest_structured(self, obs: Observation, evidence: Evidence,
                           data: dict[str, Any], changes: dict[str, Any]) -> None:
        target = str(data.get("url") or data.get("target") or data.get("host") or "")
        if not target:
            return
        host = host_from_url(target) or target
        host_node = self._upsert_node(
            obs, evidence, "host", host,
            summary="目标主机/域名", attributes={"address": observed(host)},
            changes=changes,
        )
        self._ingest_web(obs, evidence, target, data.get("artifacts") or {}, host_node, changes)

    # ---- entity helpers ------------------------------------------------
    def _find_node(self, engagement_id: str, type_id: str, name: str) -> Optional[Node]:
        for n in self.db.list_nodes(engagement_id, type_id=type_id):
            if n.name == name:
                return n
        return None

    def _upsert_node(self, obs: Observation, evidence: Evidence, type_id: str,
                     name: str, **kw: Any) -> Node:
        changes = kw.pop("changes", None)
        existing = self._find_node(obs.engagement_id, type_id, name)
        incoming_attrs = kw.get("attributes") or {}
        if existing is not None:
            # preserve evidence; detect direct conflicts instead of overwriting.
            for key, val in incoming_attrs.items():
                old = existing.attributes.get(key)
                if (isinstance(old, dict) and isinstance(val, dict)
                        and old.get("state") == "observed"
                        and val.get("state") == "observed"
                        and old.get("value") != val.get("value")):
                    existing.attributes[key] = {
                        "value": val.get("value"),
                        "previous": old.get("value"),
                        "state": "conflicted",
                        "evidence_refs": [evidence.id],
                    }
                else:
                    existing.attributes[key] = val
            for ev in (existing.evidence_refs or []) + [evidence.id]:
                if ev not in existing.evidence_refs:
                    existing.evidence_refs.append(ev)
            existing.updated_at = now()
            existing.version += 1
            node, _ = self.db.upsert_node(existing)
            if changes is not None:
                changes["nodes_updated"].append(node.id)
            self.db.record_change(
                obs.engagement_id, operation="update", object_type="node",
                object_id=node.id, before=None, after=node.to_dict(),
                reason="new observation merged", actor="plugin",
            )
            return node

        node = Node.new(
            obs.engagement_id, type_id, name,
            summary=kw.get("summary", ""),
            attributes=incoming_attrs,
            evidence_refs=[evidence.id],
            status=kw.get("status", "partially_observed"),
        )
        node, created = self.db.upsert_node(node)
        if changes is not None:
            changes["nodes_created" if created else "nodes_updated"].append(node.id)
        self.db.record_change(
            obs.engagement_id, operation="create" if created else "update",
            object_type="node", object_id=node.id, before=None,
            after=node.to_dict(), reason="observation", actor="plugin",
        )
        return node

    def _upsert_relation(self, obs: Observation, evidence: Evidence,
                         source_id: str, target_id: str, type_id: str,
                         **kw: Any) -> Relation:
        changes = kw.pop("changes", None)
        existing = next(
            (r for r in self.db.list_relations(obs.engagement_id) if
             r.source_id == source_id and r.target_id == target_id and r.type_id == type_id),
            None,
        )
        if existing is not None:
            if evidence.id not in existing.evidence_refs:
                existing.evidence_refs.append(evidence.id)
            existing.confidence = max(existing.confidence, float(kw.get("confidence", 0.5)))
            existing.updated_at = now()
            existing.version += 1
            rel, _ = self.db.upsert_relation(existing)
            if changes is not None:
                changes["relations_updated"].append(rel.id)
            return rel
        rel = Relation.new(
            obs.engagement_id, source_id, target_id, type_id,
            conditions=kw.get("conditions") or [],
            evidence_refs=[evidence.id],
            confidence=float(kw.get("confidence", 0.5)),
            status=kw.get("status", "observed"),
        )
        rel, created = self.db.upsert_relation(rel)
        if changes is not None:
            changes["relations_created" if created else "relations_updated"].append(rel.id)
        self.db.record_change(
            obs.engagement_id, operation="create" if created else "update",
            object_type="relation", object_id=rel.id, before=None,
            after=rel.to_dict(), reason="observation", actor="plugin",
        )
        return rel

    @staticmethod
    def _summary(obs: Observation) -> str:
        data = obs.data or {}
        if obs.content_kind == "tool_execution":
            return f"{data.get('tool')} {data.get('target') or ''} {str(data.get('output') or '')[:300]}"
        return json.dumps(data, ensure_ascii=False)[:500]
