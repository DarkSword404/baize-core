"""知识图谱操作层：实体抽取、提及边、事实落库、邻域扩展、图谱快照。

本层只做确定性的图结构与时间语义维护（Zep/Graphiti 风格）：
- 实体带 first_seen/last_seen 与来源 episode 的 mention 计数；
- 关系边带 valid_from/invalid_at 时间有效窗，旧边作废不删除；
- 邻域扩展用于检索时的 Semantic Memory 召回。
LLM 负责从文本提炼结构化关系（见 learn.py），本层负责存储与查询。

图上的知识有两类来源：
- 本体（taxonomy）：预置的安全知识树，节点是漏洞类型 / 场景 / 技术栈组件，
  边是 IS_A（层级）与 APPLIES_TO（什么场景用什么打法），由 materialize_taxonomy
  一次性写入——即使某类漏洞从未遇到过，节点也在图上，查询才有的放矢；
- 实例：每次任务从 Episode / 经验文本里确定性抽出的实体，以及内容物 → 实体的
  MENTIONS 边。

只存类型级知识，不存目标资产（IP / 域名 / 产品版本）——理由见
extract_entities_from_text 与 taxonomy 的模块说明。
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from . import taxonomy
from .models import REL_MENTIONS, REL_SUPPORTS, new_entity, new_relation
from .store import MemoryStore
from .utils import new_id, now_iso

logger = logging.getLogger("baize.memory.graph")

# 只保留 CVE：公开的漏洞编号是可复用知识（「遇到 CVE-2021-44228 走 Log4j 打法」），
# 不属于目标资产。IP / 域名 / 产品版本一律不抽，理由见 extract_entities_from_text。
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)

_ENTITY_KIND_BY_PREFIX = {
    "cve": "cve",
    "vuln": "vuln",
    "scenario": "scenario",
    "component": "component",
    "category": "category",
}

# 已停抽的资产类实体。保留这份清单只为一个目的：清理历史数据——此前落库的
# 资产实体要靠它识别（见 purge_asset_entities）。
_ASSET_KINDS = ("ip", "domain", "product", "port", "account")


def _mentions(low: str, alias: str) -> bool:
    """别名命中判定：纯 ASCII 用词边界，中文用子串（中文无词边界）。

    判定规则实现在本体层（taxonomy.mentions），此处仅转发，避免两处各写一份
    而逐渐分叉。
    """
    return taxonomy.mentions(low, alias)


def extract_entities_from_text(text: str) -> dict[str, dict]:
    """从一段文本确定性抽取实体，返回 {key: {key, kind, label, aliases}}。

    只抽**类型级知识**：漏洞类型、场景、技术栈组件，以及 CVE 编号。

    不再抽 IP / 域名 / 产品版本——那是目标资产实例，有两个问题：换一次任务就
    全部失效（记住「目标 A 的 8080 跑着 Shiro」对打下一个目标毫无帮助）；把客户
    域名、IP 长期留在记忆库里本身就是敏感信息，违背数据最小化。类型级知识
    （「遇到 Shiro 试反序列化」「登录框先试弱口令」）才跨任务复用，这才是知识
    图谱该存的东西。

    仅做可验证的模式识别与本体别名匹配，避免幻觉；语义关系由 LLM 层负责。
    """
    if not text:
        return {}
    out: dict[str, dict] = {}

    def add(kind: str, value: str, label: str = "", alias: str = "") -> None:
        v = value.strip().lower()
        if not v:
            return
        key = f"{kind}:{v}"
        if key not in out:
            out[key] = new_entity(key, kind, label or v,
                                  aliases=[alias] if alias and alias.lower() != v else [])
        elif alias and alias.lower() != v and alias not in out[key]["aliases"]:
            out[key]["aliases"].append(alias)

    for m in _CVE_RE.findall(text):
        add("cve", m.upper())
    # 本体节点：把文本里实际的写法也记为别名，于是「反代入口」与「反向代理」
    # 两种说法会累积到同一个节点上，下次换种叫法查询也能命中——这正是「同类
    # 漏洞换个说法」时字面匹配失效的解法。
    for key, hit in taxonomy.match_pairs(text):
        n = taxonomy.node(key)
        add(n["kind"], key, label=n["label"], alias=hit)
    return out


def materialize_taxonomy(store: MemoryStore, force: bool = False) -> int:
    """把安全知识本体的节点与语义边写入图谱，返回写入的实体数。

    本体是**预置**的：即使某类漏洞一次都没遇到过，节点也在图上，查询才能命中
    它并沿 IS_A / APPLIES_TO 找到近邻。这与「只记录出现过的实体」相反——后者在
    冷启动时图是空的，检索只能退化成纯字面匹配，而字面匹配恰恰在「同类漏洞换个
    说法」时失效，那正是最需要记忆的场景。

    force=False 时若根节点已在则跳过：本体是静态的，没必要每次启动都重写磁盘。
    """
    if not force and store.get_entity(taxonomy.entity_key(taxonomy.ROOT)):
        return 0
    n = 0
    for nd in taxonomy.all_nodes():
        store.upsert_entity(new_entity(
            key=taxonomy.entity_key(nd["key"]), kind=nd["kind"],
            label=nd["label"], aliases=list(nd["aliases"]),
            props={"taxonomy": True, "parent": nd["parent"]}))
        n += 1
    for rel_type, src, tgt in taxonomy.semantic_edges():
        store.add_relation(new_relation(
            rid=new_id("rt"), rel_type=rel_type, source=src, target=tgt,
            provenance={"episode_id": "", "at": now_iso()},
            props={"taxonomy": True}))
    logger.info("安全知识本体落库：%d 个节点", n)
    return n


def purge_asset_entities(store: MemoryStore) -> dict:
    """清理历史落库的资产实体（ip/domain/product/port/account）及其边。

    抽取侧已经停抽，但**已落库的数据不会自己消失**，所以要显式清一次。连边一起
    删是必须的（理由见 store.delete_entities）。

    只删图谱里的实体，不动经验/事实正文——正文是证据原文，改写证据会破坏可
    回溯性；是否展示、是否脱敏应由上层决定。
    """
    keys = [e["key"] for e in store.list_entities()
            if e.get("kind") in _ASSET_KINDS]
    removed = store.delete_entities(keys)
    logger.info("清理资产实体 %d 个（及其关联边）", removed)
    return {"removed_entities": removed, "kinds": list(_ASSET_KINDS)}


class KnowledgeGraph:
    """把 episode / experience 与实体、事实编织成可查询的时间图。"""

    def __init__(self, store: MemoryStore):
        self.store = store

    def ensure_taxonomy(self, force: bool = False) -> int:
        """确保安全知识本体已落库（幂等），返回写入的实体数。

        检索前调用一次即可：根节点在就跳过，不必每次写盘。
        """
        if force:
            return materialize_taxonomy(self.store, force=True)
        if self.store.get_entity(taxonomy.entity_key(taxonomy.ROOT)):
            return 0
        return materialize_taxonomy(self.store)

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
