"""知识图谱操作层：实体抽取、提及边、事实落库、邻域扩展、图谱快照。

本层只做确定性的图结构与时间语义维护（Zep/Graphiti 风格）：
- 实体带 first_seen/last_seen 与来源 episode 的 mention 计数；
- 关系边带 valid_from/invalid_at 时间有效窗，旧边作废不删除；
- 邻域扩展用于检索时的 Semantic Memory 召回。
LLM 负责从文本提炼结构化关系（见 learn.py），本层负责存储与查询。
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from .models import (REL_MENTIONS, REL_SUPPORTS, new_entity, new_relation)
from .store import MemoryStore
from .utils import new_id, now_iso

logger = logging.getLogger("baize.memory.graph")

# 确定性安全目标实体模式：CVE / IP / 域名 / IP:端口 / 弱口令特征等
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPPORT_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b")
_DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
                        r"(?:com|cn|net|org|io|dev|edu|gov|top|xyz|info|cc)\b", re.IGNORECASE)
# 常用中间件/框架指纹（产品实体），可扩展
_PRODUCTS = (
    "nginx", "apache", "tomcat", "iis", "weblogic", "jboss", "shiro", "struts2",
    "thinkphp", "wordpress", "joomla", "drupal", "phpmyadmin", "mysql", "redis",
    "postgresql", "mssql", "mongodb", "elasticsearch", "kibana", "grafana", "jenkins",
    "gitlab", "docker", "kubernetes", "metasploit", "sqlmap", "nmap", "nessus",
    "fortinet", "f5", "citrix", "fastjson", "log4j", "spring", "laravel", "weixin",
    "wechat", "alibaba", "windows", "linux", "ubuntu", "centos", "debian", "kali",
)

_ENTITY_KIND_BY_PREFIX = {
    "ip": "ip",
    "domain": "domain",
    "cve": "cve",
    "product": "product",
    "port": "port",
    "account": "account",
}


def extract_entities_from_text(text: str) -> dict[str, dict]:
    """从一段文本确定性抽取实体，返回 {key: {key, kind, label, aliases}}。

    仅做可验证的模式识别，避免幻觉；语义关系由 LLM 层负责。
    """
    if not text:
        return {}
    out: dict[str, dict] = {}
    src = text

    def add(kind: str, value: str, label: str = "", alias: str = "") -> None:
        v = value.strip().lower()
        if not v:
            return
        key = f"{kind}:{v}"
        if key not in out:
            out[key] = new_entity(key, kind, label or v, aliases=[alias] if alias and alias.lower() != v else [])
        elif alias and alias.lower() != v and alias not in out[key]["aliases"]:
            out[key]["aliases"].append(alias)

    # IP:端口 先于纯 IP，避免被消费
    for m in _IPPORT_RE.findall(src):
        ip, _, port = m.partition(":")
        add("ip", ip)
        key = f"ip:{ip.lower()}"
        if key in out and port not in out[key]["aliases"]:
            out[key]["aliases"].append(f"port {port}")
    for m in _IPV4_RE.findall(src):
        add("ip", m)
    for m in _DOMAIN_RE.findall(src):
        add("domain", m)
    for m in _CVE_RE.findall(src):
        add("cve", m.upper())
    low = src.lower()
    for p in _PRODUCTS:
        for m in re.finditer(rf"\b{re.escape(p)}\b", low):
            add("product", p, label=p)
            # 尽量带版本：如 nginx/1.18.0、nginx 1.18.0
            m = re.search(rf"\b{re.escape(p)}\b\s*(?:/|v?)\s*([0-9][0-9A-Za-z.\-]*)", low)
            if m:
                ver = m.group(1)
                if ver and not re.fullmatch(r"\d", ver):
                    add("product", f"{p} {ver}", label=f"{p} {ver}", alias=p)
            break
    return out


class KnowledgeGraph:
    """把 episode / experience 与实体、事实编织成可查询的时间图。"""

    def __init__(self, store: MemoryStore):
        self.store = store

    # ---- 落库 -------------------------------------------------------------
    def record_entities(self, episode_id: str, texts: Iterable[str]) -> list[str]:
        """从 episode 相关文本中抽取实体并 upsert，返回 entity keys。"""
        seen: dict[str, dict] = {}
        for text in texts:
            for key, ent in extract_entities_from_text(text).items():
                seen.setdefault(key, ent)
        keys: list[str] = []
        for key, ent in seen.items():
            ent.setdefault("properties", {})
            mentions = ent["properties"].get("mentions", [])
            if episode_id not in mentions:
                mentions.append(episode_id)
            ent["properties"]["mentions"] = mentions[-200:]
            self.store.upsert_entity(ent)
            keys.append(key)
        return keys

    def link_mentions(self, src_kind: str, src_id: str,
                      entity_keys: Iterable[str], provenance_episode: str = "") -> int:
        """在内容物与实体之间建 MENTIONS 边（episode/experience → entity）。"""
        n = 0
        for key in set(entity_keys):
            rel = new_relation(
                rid=new_id("rm"),
                rel_type=REL_MENTIONS,
                source=f"{src_kind}:{src_id}",
                target=key,
                provenance={"episode_id": provenance_episode, "at": now_iso()},
            )
            self.store.add_relation(rel)
            n += 1
        return n

    def record_relation(self, rel_type: str, src_id: str, tgt_id: str,
                        provenance_episode: str = "", props: dict | None = None) -> dict:
        """任意两内容物/实体之间的关系（经验↔经验、事实↔经验等）。"""
        rel = new_relation(rid=new_id("rr"), rel_type=rel_type,
                           source=src_id, target=tgt_id,
                           provenance={"episode_id": provenance_episode, "at": now_iso()},
                           props=props)
        self.store.add_relation(rel)
        return rel

    # ---- 查询 -------------------------------------------------------------
    def expand(self, entity_keys: Iterable[str], hop: int = 1,
               max_nodes: int = 60) -> dict:
        """语义记忆召回：从命中实体出发沿有效边扩展邻域。"""
        visited: set[str] = set()
        frontier: list[str] = list(entity_keys)
        relations: list[dict] = []
        for _ in range(max(1, hop)):
            next_frontier: list[str] = []
            for key in frontier:
                if key in visited:
                    continue
                visited.add(key)
                for r in self.store.list_relations(key):
                    if r["id"] not in {x["id"] for x in relations}:
                        relations.append(r)
                    other = r["target"] if r["source"] == key else r["source"]
                    if other not in visited and other not in next_frontier:
                        next_frontier.append(other)
                if len(visited) >= max_nodes:
                    break
            frontier = next_frontier
        entities = {e["key"]: e for e in self.store.list_entities()
                    if e["key"] in visited}
        return {"entities": list(entities.values()), "relations": relations}

    def snapshot(self, max_entities: int = 300,
                 include_relations: bool = True) -> dict:
        """整图快照（前端可视化用）：实体 + 有效关系。"""
        entities = self.store.list_entities(limit=max_entities)
        ekeys = {e["key"] for e in entities}
        rels = []
        if include_relations:
            rels = [r for r in self.store.list_relations()
                    if r["source"] in ekeys or r["target"] in ekeys]
            # 限制每个实体的边数量避免爆炸
            cap: dict[str, int] = {}
            rels_ok: list[dict] = []
            for r in rels:
                a, b = r["source"], r["target"]
                if cap.get(a, 0) < 20 and cap.get(b, 0) < 20:
                    rels_ok.append(r)
                    cap[a] = cap.get(a, 0) + 1
                    cap[b] = cap.get(b, 0) + 1
            rels = rels_ok
        return {"entities": entities, "relations": rels}
