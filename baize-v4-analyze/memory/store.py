"""MemoryStore：memory 子系统的持久化真相源。

目录结构（根：BAIZE_MEMORY_DIR 或 ~/.baize/memory）：
    episodes/           一次任务一条 JSON（Episodic，只追加）
    experiences/        经验一条 JSON（Experience）
    facts/              结构化语义事实一条 JSON（Semantic）
    graph/entities.json 实体主索引
    graph/relations.json 关系边（时间有效窗）

写入全部原子（tmp + os.replace）；进程内维护索引缓存，单进程读写即时一致。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

from .models import (CONTENT_STATUSES, EPISODE_STATUSES, REL_STATUSES,
                     new_relation)
from .utils import atomic_write_text, data_root, json_dump, json_load, now_iso

logger = logging.getLogger("baize.memory.store")

COLLECTIONS = ("episodes", "experiences", "facts")
_GRAPHS = ("entities", "relations")


class MemoryStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = (root or data_root())
        for name in COLLECTIONS:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / "graph").mkdir(parents=True, exist_ok=True)
        # 索引缓存（惰性构建，写入即时同步）
        self._graph_loaded = False
        self._entities: dict[str, dict] = {}
        self._relations: dict[str, dict] = {}
        self._index: dict[str, dict[str, dict]] = {c: {} for c in COLLECTIONS}
        self._lists_loaded = False

    # ---- 文件层 ----------------------------------------------------------
    def _path(self, kind: str, oid: str) -> Path:
        return self.root / kind / f"{oid}.json"

    def _reload_graph(self) -> None:
        if self._graph_loaded:
            return
        self._entities = json_load(self.root / "graph" / "entities.json", {}) or {}
        self._relations = json_load(self.root / "graph" / "relations.json", {}) or {}
        self._graph_loaded = True

    def _persist_graph(self) -> None:
        json_dump(self.root / "graph" / "entities.json", self._entities)
        json_dump(self.root / "graph" / "relations.json", self._relations)

    def _load_collection(self, kind: str) -> None:
        if self._lists_loaded:
            return
        for col in COLLECTIONS:
            cache: dict[str, dict] = {}
            for f in sorted((self.root / col).glob("*.json")):
                rec = json_load(f, None)
                if isinstance(rec, dict) and rec.get("id"):
                    cache[rec["id"]] = rec
            self._index[col] = cache
        self._lists_loaded = True

    def _save_record(self, kind: str, rec: dict) -> None:
        if not rec.get("id"):
            raise ValueError(f"{kind} 记录缺少 id")
        json_dump(self._path(kind, rec["id"]), rec)
        self._load_collection(kind)
        self._index[kind][rec["id"]] = rec

    def _get_record(self, kind: str, oid: str) -> Optional[dict]:
        self._load_collection(kind)
        if oid in self._index[kind]:
            return self._index[kind][oid]
        rec = json_load(self._path(kind, oid), None)
        if isinstance(rec, dict) and rec.get("id"):
            self._index[kind][oid] = rec
            return rec
        return None

    def _list_records(self, kind: str) -> list[dict]:
        self._load_collection(kind)
        return list(self._index[kind].values())

    def reload(self) -> None:
        """多进程改动后强制重建缓存（运行期一般不调用）。"""
        self._graph_loaded = False
        self._lists_loaded = False
        self._index = {c: {} for c in COLLECTIONS}
        self._entities = {}
        self._relations = {}
        self._reload_graph()
        for c in COLLECTIONS:
            self._load_collection(c)

    # ---- Episodes（只追加） -----------------------------------------------
    def save_episode(self, rec: dict) -> dict:
        self._save_record("episodes", rec)
        return rec

    def get_episode(self, episode_id: str) -> Optional[dict]:
        return self._get_record("episodes", episode_id)

    def list_episodes(self, limit: int = 200) -> list[dict]:
        recs = self._list_records("episodes")
        recs.sort(key=lambda r: r.get("end_at") or r.get("start_at") or "", reverse=True)
        return recs[:limit]

    # ---- Experiences -------------------------------------------------------
    def save_experience(self, rec: dict) -> dict:
        rec["updated_at"] = now_iso()
        self._save_record("experiences", rec)
        return rec

    def get_experience(self, exp_id: str) -> Optional[dict]:
        return self._get_record("experiences", exp_id)

    def list_experiences(self, status: Optional[str] = None,
                         scope: Optional[str] = None) -> list[dict]:
        recs = self._list_records("experiences")
        if status:
            recs = [r for r in recs if r.get("status") == status]
        if scope:
            recs = [r for r in recs if r.get("scope") == scope]
        recs.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
        return recs

    # ---- Facts（Semantic） ---------------------------------------------------
    def save_fact(self, rec: dict) -> dict:
        rec["updated_at"] = now_iso()
        self._save_record("facts", rec)
        return rec

    def get_fact(self, fact_id: str) -> Optional[dict]:
        return self._get_record("facts", fact_id)

    def list_facts(self, status: Optional[str] = None,
                   include_invalid: bool = False) -> list[dict]:
        recs = self._list_records("facts")
        if not include_invalid:
            recs = [r for r in recs if r.get("status") != "invalidated"]
        if status:
            recs = [r for r in recs if r.get("status") == status]
        recs.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
        return recs

    # ---- Knowledge Graph：实体 ---------------------------------------------
    def upsert_entity(self, ent: dict) -> dict:
        self._reload_graph()
        key = ent["key"]
        old = self._entities.get(key)
        if old:
            merged = dict(old)
            merged["kind"] = ent.get("kind") or old.get("kind")
            merged["label"] = ent.get("label") or old.get("label") or key
            merged["aliases"] = sorted(set(old.get("aliases", []) + ent.get("aliases", [])))
            props = dict(old.get("properties", {}))
            for k, v in (ent.get("properties") or {}).items():
                if v and not props.get(k):
                    props[k] = v
            merged["properties"] = props
            merged["last_seen"] = now_iso()
            ent = merged
        else:
            ent.setdefault("first_seen", now_iso())
            ent.setdefault("last_seen", now_iso())
        self._entities[key] = ent
        self._persist_graph()
        return ent

    def get_entity(self, key: str) -> Optional[dict]:
        self._reload_graph()
        return self._entities.get(key)

    def list_entities(self, limit: int = 2000) -> list[dict]:
        self._reload_graph()
        recs = sorted(self._entities.values(),
                      key=lambda e: len(e.get("properties", {}).get("mentions", []) or []),
                      reverse=True)
        return recs[:limit]

    # ---- Knowledge Graph：关系边 ---------------------------------------------
    def add_relation(self, rel: dict, dedupe: bool = True) -> dict:
        """新增关系；若同 (type, source, target) 且当前有效则合并 occurrence。"""
        self._reload_graph()
        if dedupe:
            for r in self._relations.values():
                if (r.get("type") == rel.get("type")
                        and r.get("source") == rel.get("source")
                        and r.get("target") == rel.get("target")
                        and r.get("status") == "active"):
                    r["occurrences"] = int(r.get("occurrences", 1)) + 1
                    r["last_seen"] = now_iso()
                    self._persist_graph()
                    return r
        self._relations[rel["id"]] = rel
        self._persist_graph()
        return rel

    def invalidate_relation(self, rid: str, at: str = "") -> Optional[dict]:
        self._reload_graph()
        rel = self._relations.get(rid)
        if not rel:
            return None
        rel["status"] = "invalidated"
        rel["invalid_at"] = at or now_iso()
        self._persist_graph()
        return rel

    def get_relation(self, rid: str) -> Optional[dict]:
        self._reload_graph()
        return self._relations.get(rid)

    def list_relations(self, entity_key: Optional[str] = None,
                       include_invalid: bool = False) -> list[dict]:
        self._reload_graph()
        recs = list(self._relations.values())
        if not include_invalid:
            recs = [r for r in recs if r.get("status") != "invalidated"]
        if entity_key:
            recs = [r for r in recs
                    if r.get("source") == entity_key or r.get("target") == entity_key]
        recs.sort(key=lambda r: r.get("last_seen") or "", reverse=True)
        return recs

    # ---- 统计 --------------------------------------------------------------
    def stats(self) -> dict:
        episodes = self._list_records("episodes")
        experiences = self._list_records("experiences")
        facts = self._list_records("facts")
        self._reload_graph()
        return {
            "episodes": len(episodes),
            "experiences": len(experiences),
            "experiences_active": sum(1 for r in experiences if r.get("status") == "active"),
            "experiences_draft": sum(1 for r in experiences if r.get("status") == "draft"),
            "facts": len(facts),
            "entities": len(self._entities),
            "relations": len(self._relations),
        }
