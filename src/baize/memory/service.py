"""MemoryService：对外统一门面。

所有上层（Agent 注入、REST API、未来的知识图谱前端）只与 MemoryService 交互。
对外方法按设计规格中的记忆类型与生命周期组织：
    learn*            —— 产生 Episode，并从中学习（经验/实体/证据）
    retrieve / inject —— 混合检索 + 上下文注入
    revise/supersede  —— 经验演进（状态机 + 谱系 + 审计）
    consolidate       —— 同主题经验合并概括
    graph 系列        —— 知识图谱读取（时间窗实体/关系）
    list/get/stats    —— 各类记忆的读取
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

from .episode import EpisodeBuilder, build_from_log
from .evolve import consolidate as _consolidate
from .evolve import revise as _revise
from .evolve import set_status as _set_status
from .evolve import supersede as _supersede
from .graph import KnowledgeGraph
from .learn import MemoryLearner
from .models import EpisodeRecord
from .retrieve import MemoryRetriever
from .store import MemoryStore
from .utils import new_id, now_iso

logger = logging.getLogger("baize.memory.service")


class MemoryService:
    def __init__(self, root: Optional[Path] = None, client=None):
        self.store = MemoryStore(root)
        self.graph = KnowledgeGraph(self.store)
        self.client = client
        self.retriever = MemoryRetriever(self.store, self.graph)
        self.learner = MemoryLearner(self.store, self.graph, client)

    # ---- 基础读取 ----------------------------------------------------------
    @property
    def embedding_client(self):
        return self.client

    def stats(self) -> dict:
        return self.store.stats()

    def list_experiences(self, status: Optional[str] = None,
                         scope: Optional[str] = None,
                         include_superseded: bool = False) -> list[dict]:
        recs = self.store.list_experiences(status=status, scope=scope)
        if not include_superseded:
            recs = [r for r in recs if r.get("status") != "superseded"]
        return recs

    def get_experience(self, exp_id: str) -> Optional[dict]:
        return self.store.get_experience(exp_id)

    def list_episodes(self, limit: int = 100) -> list[dict]:
        return self.store.list_episodes(limit=limit)

    def get_episode(self, episode_id: str) -> Optional[dict]:
        return self.store.get_episode(episode_id)

    def list_facts(self, status: Optional[str] = None) -> list[dict]:
        return self.store.list_facts(status=status)

    def list_entities(self, limit: int = 1000) -> list[dict]:
        return self.store.list_entities(limit=limit)

    def list_relations(self, entity_key: Optional[str] = None) -> list[dict]:
        return self.store.list_relations(entity_key=entity_key)

    # ---- 学习 --------------------------------------------------------------
    async def learn_events(self, *, events: list[dict], task: str = "",
                           target: str = "", agent_key: str = "",
                           session_id: str = "", result_text: str = "",
                           error_text: str = "", status: str = "success",
                           user_message: str = "", start_at: str = "",
                           client=None) -> dict:
        ep = build_from_log(task=task, target=target, events=events,
                            result_text=result_text, error_text=error_text,
                            status=status, agent_key=agent_key,
                            session_id=session_id, start_at=start_at)
        learner = self.learner if client is None else MemoryLearner(
            self.store, self.graph, client)
        return await learner.learn(ep, user_message=user_message)

    async def learn_episode(self, episode: EpisodeRecord,
                            client=None) -> dict:
        learner = self.learner if client is None else MemoryLearner(
            self.store, self.graph, client)
        return await learner.learn(episode)

    # ---- 手工沉淀 ----------------------------------------------------------
    def create_experience(self, *, title: str, content: str,
                          tags: Optional[list[str]] = None,
                          kind: str = "method", scope: str = "global",
                          agent_key: str = "", source_session_id: str = "",
                          importance: int = 3) -> dict:
        """手工新增一条经验（直接 active；由人负责内容质量）。"""
        ts = now_iso()
        rec = {
            "id": new_id("exp"),
            "title": (title or "").strip(),
            "content": (content or "").strip(),
            "tags": list(tags or []),
            "kind": kind if kind in ("method", "lesson", "intel") else "method",
            "status": "active",
            "confidence": 0.9,
            "scope": scope,
            "agent_key": agent_key,
            "source_session_id": source_session_id,
            "episode_id": "",
            "evidence": [],
            "supersedes": [],
            "replaced_by": "",
            "history": [{"action": "manual-create", "at": ts, "actor": "api"}],
            "importance": max(0, int(importance)),
            "hit_count": 0,
            "useful_count": 0,
            "noise_count": 0,
            "last_hit_at": "",
            "created_at": ts,
            "updated_at": ts,
            "source": "api",
        }
        if not rec["title"] or not rec["content"]:
            raise ValueError("title 与 content 不能为空")
        return self.store.save_experience(rec)

    def open_episode(self, *, task: str = "", target: str = "",
                     agent_key: str = "", session_id: str = "",
                     start_at: str = "") -> EpisodeBuilder:
        """流式学习中先开 Episode，逐步塞事件，最后 conclude + learn。"""
        return EpisodeBuilder(task=task, target=target, agent_key=agent_key,
                              session_id=session_id, start_at=start_at)

    # ---- 检索 --------------------------------------------------------------
    def search(self, query: str, k_experiences: int = 5,
               include_episodes: bool = False) -> dict:
        return self.retriever.search(query, k_experiences=k_experiences,
                                     include_episodes=include_episodes)

    def inject_block(self, query: str, include_episodes: bool = False) -> str:
        return self.retriever.build_injection(query,
                                              include_episodes=include_episodes)

    def recall(self, query: str, k: int = 5) -> dict:
        """检索并注入：一次检索同时返回注入文本与命中的经验 id（用于评价闭环）。"""
        data = self.retriever.search(query, k_experiences=k)
        ids = [e.get("id", "") for e in data.get("experiences", []) if e.get("id")]
        block = self.retriever.build_injection(query, data=data)
        return {"block": block, "ids": ids}

    # ---- 评价闭环（命中 / 采纳 / 无用） --------------------------------------
    def record_hits(self, exp_ids: Iterable[str]) -> int:
        """经验被注入到上下文时计数（只说明“被召回”，不代表有用）。"""
        n = 0
        for eid in exp_ids or []:
            rec = self.store.get_experience(eid)
            if rec is None:
                continue
            rec["hit_count"] = int(rec.get("hit_count", 0)) + 1
            rec["last_hit_at"] = now_iso()
            self.store.save_experience(rec)
            n += 1
        return n

    def record_feedback(self, exp_id: str, useful: bool, *, actor: str = "api",
                        note: str = "") -> Optional[dict]:
        """对经验打分：有用/无用。

        - 有用次数会提升后续检索排序权重；
        - 连续被判无用（≥5 次且从未有用）自动降级为 draft，交人工复核，
          不物理删除（保留谱系与审计）。
        """
        rec = self.store.get_experience(exp_id)
        if rec is None:
            return None
        if useful:
            rec["useful_count"] = int(rec.get("useful_count", 0)) + 1
        else:
            rec["noise_count"] = int(rec.get("noise_count", 0)) + 1
        history = rec.setdefault("history", [])
        history.append({"action": "feedback", "at": now_iso(), "actor": actor,
                        "useful": bool(useful), "note": note})
        noise = int(rec.get("noise_count", 0))
        useful_n = int(rec.get("useful_count", 0))
        if not useful and noise >= 5 and useful_n == 0 and rec.get("status") == "active":
            rec["status"] = "draft"
            history.append({"action": "status", "at": now_iso(), "actor": "system",
                            "from": "active", "to": "draft",
                            "note": "连续被判无用，自动降级为草稿待复核"})
        return self.store.save_experience(rec)

    # ---- 演进 --------------------------------------------------------------
    def revise(self, target_id: str, *, actor: str = "api", note: str = "",
               fields: Optional[dict] = None) -> Optional[dict]:
        return _revise(self.store, target_id, actor=actor, note=note,
                       fields=fields, graph=self.graph)

    def set_status(self, target_id: str, status: str, *, actor: str = "api",
                   note: str = "") -> Optional[dict]:
        return _set_status(self.store, target_id, status, actor=actor,
                           note=note, graph=self.graph)

    def supersede(self, old_ids: Iterable[str], new_id_value: str, *,
                  actor: str = "api", note: str = "") -> list[dict]:
        return _supersede(self.store, old_ids, new_id_value, actor=actor,
                          note=note, graph=self.graph)

    async def consolidate(self, ids: list[str], *, actor: str = "api",
                          auto_commit: bool = False) -> Optional[dict]:
        return await _consolidate(self.store, ids, actor=actor,
                                  client=self.client, auto_commit=auto_commit,
                                  graph=self.graph)

    # ---- 图谱视图 --------------------------------------------------------------
    def graph_snapshot(self, include_content: bool = True) -> dict:
        """前端可视化用的图谱快照。

        返回统一节点/边结构：
          nodes  —— experience(内容)+entity（实体会带 kind），episode 简要节点
          edges  —— 内容物↔实体 MENTIONS、内容物之间语义关系（SUPPORTS 等）
        """
        from .models import REL_MENTIONS

        base = self.graph.snapshot(max_entities=400)
        nodes: list[dict] = []
        edges: list[dict] = []
        seen_entity = {e["key"] for e in base["entities"]}

        def nid(v: str) -> str:
            if v.startswith(("experience:", "episode:")):
                return v
            return f"entity:{v}"

        # 实体节点
        for e in base["entities"]:
            nodes.append({
                "id": f"entity:{e['key']}", "label": e.get("label", e["key"]),
                "kind": "entity", "group": e.get("kind", "entity"),
                "entity_key": e["key"],
                "mentions": len(e.get("properties", {}).get("mentions", []) or []),
            })
        # 经验节点
        exp_nodes: dict[str, dict] = {}
        for exp in self.store.list_experiences():
            if include_content and exp.get("status") == "superseded":
                continue
            exp_nodes[exp["id"]] = {
                "id": f"experience:{exp['id']}",
                "label": exp.get("title", ""), "kind": "experience",
                "group": "experience", "status": exp.get("status"),
                "confidence": exp.get("confidence", 0), "exp_id": exp["id"],
                "tags": exp.get("tags", [])[:6],
            }
        nodes.extend(exp_nodes.values())

        # 边：关系 + 内容-实体 提及
        for r in base["relations"]:
            src, tgt = nid(r["source"]), nid(r["target"])
            edges.append({
                "source": src, "target": tgt, "type": r.get("type", ""),
                "valid_from": r.get("valid_from", ""),
                "invalid_at": r.get("invalid_at", ""),
            })
        # 经验节点与提及实体的连接（relation 中 source 可能是 content id）
        return {"nodes": nodes, "edges": edges, "stats": self.stats()}
