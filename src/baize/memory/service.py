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

from . import taxonomy
from .embed import embedder, experience_text, fingerprint
from .episode import EpisodeBuilder, build_from_log
from .evolve import auto_consolidate as _auto_consolidate
from .evolve import consolidate as _consolidate
from .evolve import revise as _revise
from .evolve import set_status as _set_status
from .evolve import supersede as _supersede
from .graph import KnowledgeGraph, purge_asset_entities
from .learn import MemoryLearner
from .models import EpisodeRecord
from .retrieve import MemoryRetriever
from .store import MemoryStore
from .utils import new_id, now_iso, token_dict

logger = logging.getLogger("baize.memory.service")


class MemoryService:
    def __init__(self, root: Optional[Path] = None, client=None):
        self.store = MemoryStore(root)
        self.graph = KnowledgeGraph(self.store)
        self.client = client
        self.retriever = MemoryRetriever(self.store, self.graph)
        self.learner = MemoryLearner(self.store, self.graph, client)
        # 安全知识本体一次性预置（幂等）：检索要靠它做语义扩展，节点必须先在图
        # 上，否则「某类漏洞从没遇到过」时查询会完全落空。
        self.graph.ensure_taxonomy()

    # ---- 基础读取 ----------------------------------------------------------
    @property
    def embedding_client(self):
        """语义检索用的 embedder；未配置 embedding 服务时 enabled() 为 False。

        原先这里直接返回 LLM client，是个没接上的占位——向量检索要的是
        embedding 端点，不是 chat 端点，两者模型通常也不同。
        """
        return embedder()

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
        out = await learner.learn(ep, user_message=user_message)
        # 刚学到的经验要立刻有向量，否则下一次检索的语义通道里根本看不到它。
        # 未配置 embedding 时 embed_pending 直接返回 0，不产生任何网络开销。
        await self.embed_pending(limit=20)
        return out

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

    # ---- 语义检索（可选） ---------------------------------------------------
    async def embed_pending(self, limit: int = 100) -> int:
        """为缺向量（或向量已过期）的经验补算 embedding 并落盘。

        检索路径是同步的，没法在查询时现算文档向量，所以文档向量必须先落盘。
        启用 embedding 之前写入的存量经验都没有向量，需要补一次；之后由学习
        路径增量补算。未配置 embedding 服务时直接返回 0。

        返回成功写入的条数。
        """
        em = embedder()
        if not em.enabled():
            return 0
        todo: list[tuple[dict, str]] = []
        for rec in self.store.list_experiences():
            if rec.get("status") == "superseded":
                continue
            text = experience_text(rec)
            if not text:
                continue
            fp = fingerprint(text)
            emb = rec.get("embedding") or {}
            if emb.get("fp") == fp and emb.get("v"):
                continue
            todo.append((rec, fp))
            if len(todo) >= limit:
                break
        if not todo:
            return 0
        vecs = await em.embed_async([experience_text(r) for r, _ in todo])
        if not vecs:
            return 0
        n = 0
        for (rec, fp), v in zip(todo, vecs):
            if not v:
                continue
            rec["embedding"] = {"v": v, "fp": fp}
            # 注意：save_experience 会刷新 updated_at，补算向量会被记为一次更新
            self.store.save_experience(rec)
            n += 1
        return n

    # ---- 检索 --------------------------------------------------------------
    def search(self, query: str, k_experiences: int = 5,
               include_episodes: bool = False) -> dict:
        return self.retriever.search(query, k_experiences=k_experiences,
                                     include_episodes=include_episodes)

    def inject_block(self, query: str, include_episodes: bool = False) -> str:
        return self.retriever.build_injection(query,
                                              include_episodes=include_episodes)

    def explore(self, concept: str, limit: int = 20) -> dict:
        """围绕一个知识概念浏览图谱：本体近邻 + 挂在这些节点上的经验。

        检索回答的是「给一句话，该注入什么」；浏览回答的是「这个概念周围有什么」。
        排查「为什么这条经验没被召回」、或者手工查看某类漏洞积累了哪些经验时，
        后者更直接——这是知识图谱该有的用法之一，也是把图谱从「只能被算法用」
        变成「人也能看」的关键。
        """
        hits = taxonomy.match(concept or "")
        if not hits:
            # 本体没命中就退回图上已有的实体（可能带着任务里学到的自定义别名）
            hits = [e["key"].split(":", 1)[-1]
                    for e in self.retriever._match_entities(concept or "")]
        nodes = [{"key": taxonomy.entity_key(k), "label": taxonomy.label(k),
                  "kind": taxonomy.kind_of(k)} for k in hits if taxonomy.node(k)]
        related: list[dict] = []
        seen = {n["key"] for n in nodes}
        for k in hits:
            if not taxonomy.node(k):
                continue
            links: list[tuple[str, str]] = []
            p = taxonomy.parent(k)
            if p:
                links.append((p, "父分类"))
            links += [(c, "子类型") for c in taxonomy.children(k)]
            links += [(s, "同类") for s in taxonomy.siblings(k)]
            links += [(a, "适用场景" if taxonomy.kind_of(a) == taxonomy.KIND_SCENARIO
                       else "可用打法") for a in taxonomy.applies(k)]
            for rk, rel in links:
                ekey = taxonomy.entity_key(rk)
                if ekey in seen:
                    continue
                seen.add(ekey)
                related.append({"key": ekey, "label": taxonomy.label(rk),
                                "kind": taxonomy.kind_of(rk), "rel": rel})
        exps = [e for e in self.store.list_experiences()
                if e.get("status") == "active"
                and seen & set(e.get("entity_keys") or [])]
        exps.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
        return {
            "concept": concept,
            "nodes": nodes,
            "related": related,
            "experiences": [{"id": e.get("id", ""), "title": e.get("title", ""),
                             "kind": e.get("kind", ""), "tags": e.get("tags", [])}
                            for e in exps[:limit]],
        }

    def reindex_entities(self) -> dict:
        """用当前本体重新抽取所有经验的实体并重建 MENTIONS 边。

        本体结构一变（例如从「资产共现图」改成「漏洞 / 场景知识图谱」），存量
        经验的 entity_keys 还是旧结构——实测 91 条经验挂着 3750 个实体，其中
        3627 个是域名 / IP / 产品，资产清理后全是悬空引用。此时图通道等于对着
        一批永远连不上查询的节点做 BFS：检索看起来在工作，实际召回不到东西。

        所以本体升级后必须重算一次。幂等，可反复执行。
        """
        from .models import REL_MENTIONS

        exps = self.store.list_experiences()
        ids = [e.get("id", "") for e in exps if e.get("id")]
        removed = self.store.delete_relations_of(ids, rel_types=[REL_MENTIONS])
        updated = 0
        for e in exps:
            exp_id = e.get("id", "")
            if not exp_id:
                continue
            keys = self.graph.record_entities(exp_id, [experience_text(e)])
            e["entity_keys"] = keys
            self.store.save_experience(e)
            if keys:
                self.graph.link_mentions("experience", exp_id, keys)
            updated += 1
        logger.info("重挂经验实体：%d 条经验，清理旧 MENTIONS 边 %d 条",
                    updated, removed)
        return {"experiences": updated, "removed_relations": removed}

    def purge_assets(self) -> dict:
        """清理历史落库的资产实体（IP / 域名 / 产品版本）及其边。

        抽取侧已停抽，但已落库的数据不会自己消失，需要显式清一次。只动图谱，
        不动经验正文——正文是证据原文，改写它会破坏可回溯性；是否展示、是否
        脱敏应由上层决定。
        """
        return purge_asset_entities(self.store)

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

    def record_outcome(self, exp_ids: Iterable[str], final_text: str, *,
                       actor: str = "agent", min_overlap: float = 0.34) -> int:
        """本回合产出结论后，只给「结论里真的用上了」的经验记有用。

        若只要「注入过 + 本回合有产出」就记有用，useful_count 会退化成 hit_count
        的复制品：两者都只说明这条被召回过，却在排序里被算成两份证据，结果就是
        老面孔越排越前、新经验永远浮不上来。这里加一道筛——结论文本与经验标题
        的词面重叠达到阈值才算被引用，才记一次有用。

        判据用词面而非语义：结论里复述了经验的关键词，是最朴素的「用上了」证据，
        不值得再为它调一次模型。
        """
        text = (final_text or "").strip()
        if not text:
            return 0
        q = set(token_dict(text))
        if not q:
            return 0
        n = 0
        for eid in exp_ids or []:
            rec = self.store.get_experience(eid)
            if rec is None:
                continue
            title_toks = set(token_dict(rec.get("title", "") or ""))
            if not title_toks:
                continue
            if len(q & title_toks) / len(title_toks) >= min_overlap:
                self.record_feedback(eid, True, actor=actor,
                                     note="本回合结论引用了该经验")
                n += 1
        return n

    def reindex_experience_entities(self, limit: int = 500) -> int:
        """为存量经验补抽自身文本的实体——漏洞类型主要来自这里。

        经验原先只继承 episode 的实体，而 episode 文本是工具输出（域名、IP、
        版本号），「反序列化」「越权」这类漏洞类型几乎只出现在经验的总结性表述
        里，于是漏洞类型实体长期为零，图距离通道无从发挥作用。这里按「标题 +
        正文 + 标签」重抽一遍并合并，只增不减，可重复执行。
        """
        n = 0
        for exp in self.store.list_experiences()[:max(1, limit)]:
            exp_id = exp.get("id", "")
            if not exp_id:
                continue
            text = " ".join([
                exp.get("title", "") or "",
                exp.get("content", "") or "",
                " ".join(exp.get("tags") or []),
            ]).strip()
            if not text:
                continue
            added = [k for k in self.graph.record_entities(exp_id, [text])
                     if k not in (exp.get("entity_keys") or [])]
            if not added:
                continue
            exp["entity_keys"] = list(exp.get("entity_keys") or []) + added
            self.store.save_experience(exp)
            self.graph.link_mentions("experience", exp_id, added,
                                     provenance_episode=exp.get("episode_id", ""))
            n += 1
        return n

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

    async def auto_consolidate(self, *, min_group: int = 3,
                               max_groups: int = 1) -> list[dict]:
        """自动合并同类经验（由会话学习结束触发，见 agent.learn_session）。

        手动 consolidate 只挂在 API 端点，没有自动调度时同类经验只增不演化。
        达到阈值（默认同一实体下 3 条同类经验）即合并为一条更泛化的经验。
        """
        return await _auto_consolidate(self.store, client=self.client,
                                       graph=self.graph, min_group=min_group,
                                       max_groups=max_groups)

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
