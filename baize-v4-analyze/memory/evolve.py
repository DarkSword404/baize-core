"""Evolution 操作：REVISE / SUPERSEDE / INVALIDATE + 合并概括（Consolidation）。

与“只增不改”的 Episode 不同，经验是活的知识：可能被修正、被更泛化的版本
取代、被证明失效。所有演进都落在：
- 记录本体状态机 + 每次变更写入 history（可审计版本链）；
- replaced_by/supersedes 谱系指针；
- 知识图中 SUPERSEDES / CONTRADICTS 关系边（时间戳）。
本模块无 LLM 调用（consolidate 除外），全部为纯结构操作。
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from .models import REL_SUPERSEDES
from .store import MemoryStore
from .utils import new_id, now_iso

logger = logging.getLogger("baize.memory.evolve")

_EDITABLE = {"title", "content", "tags", "kind", "importance"}


def _valid_statuses():
    return ("draft", "active", "superseded", "invalidated")


def revise(store: MemoryStore, target_id: str, *, actor: str = "api",
           note: str = "", fields: Optional[dict] = None,
           graph=None) -> Optional[dict]:
    """REVISE：就地修订并留痕（不改 id，保留证据与谱系）。"""
    rec = store.get_experience(target_id) or store.get_fact(target_id)
    if rec is None:
        return None
    kind = "experience" if store.get_experience(target_id) else "fact"
    fields = fields or {}
    changed: list[str] = []
    for key, val in fields.items():
        if key not in _EDITABLE:
            continue
        if key == "tags" and not isinstance(val, list):
            continue
        if key == "importance":
            val = int(val)
        if rec.get(key) != val:
            rec[key] = val
            changed.append(key)
    if changed:
        history = rec.setdefault("history", [])
        history.append({
            "action": "revise",
            "at": now_iso(),
            "actor": actor,
            "fields": changed,
            "note": note,
        })
    if kind == "experience":
        store.save_experience(rec)
    else:
        store.save_fact(rec)
    return rec


def set_status(store: MemoryStore, target_id: str, status: str, *,
               actor: str = "api", note: str = "", graph=None) -> Optional[dict]:
    """状态迁移（含 active 确认 draft、主动失效等），自动留痕。"""
    if status not in _valid_statuses():
        raise ValueError(f"非法状态: {status}")
    rec = store.get_experience(target_id) or store.get_fact(target_id)
    if rec is None:
        return None
    kind = "experience" if store.get_experience(target_id) else "fact"
    old = rec.get("status")
    if old != status:
        rec["status"] = status
        if kind == "fact" and status in ("invalidated", "superseded"):
            rec["invalid_at"] = now_iso()
        history = rec.setdefault("history", [])
        history.append({
            "action": "status",
            "at": now_iso(),
            "actor": actor,
            "from": old,
            "to": status,
            "note": note,
        })
    if kind == "experience":
        store.save_experience(rec)
    else:
        store.save_fact(rec)
    return rec


def supersede(store: MemoryStore, old_ids: Iterable[str],
              new_id_value: Optional[str], *, actor: str = "api",
              note: str = "", graph=None) -> list[dict]:
    """把一组旧经验标记为已被 new_id_value 取代。

    - 旧经验 status=superseded、replaced_by=新 id；
    - 若提供 graph，则在知识图中记录 新→旧 的 SUPERSEDES 边（新取代旧）。
    """
    updated: list[dict] = []
    for oid in old_ids:
        rec = store.get_experience(oid)
        if rec is None or rec.get("status") == "superseded":
            continue
        rec["status"] = "superseded"
        rec["replaced_by"] = new_id_value or ""
        history = rec.setdefault("history", [])
        history.append({"action": "supersede", "at": now_iso(), "actor": actor,
                        "replaced_by": new_id_value, "note": note})
        store.save_experience(rec)
        if graph is not None and new_id_value:
            graph.record_relation(REL_SUPERSEDES,
                                  f"experience:{new_id_value}",
                                  f"experience:{oid}",
                                  provenance_episode="")
        updated.append(rec)
    return updated


async def consolidate(store: MemoryStore, ids: list[str], *, actor: str = "api",
                      client=None, auto_commit: bool = False,
                      graph=None) -> Optional[dict]:
    """CONSOLIDATION：把一组同主题经验合并为一条更泛化的新经验。

    新经验默认 status=draft（需用户确认后才取代旧条目）；若 auto_commit=True，
    直接走 supersede() 把旧条目标记为被取代。

    返回 {experience_id, status, supersedes, content}
    """
    recs: list[dict] = []
    for i in ids:
        rec = store.get_experience(i)
        if rec and rec.get("status") not in ("superseded", "invalidated"):
            recs.append(rec)
    if len(recs) < 2:
        return None

    titles = "\n".join(f"- {r.get('title','')}" for r in recs)
    content_all = "\n\n".join(
        f"{r.get('title','')}\n{r.get('content','')}" for r in recs)

    if client is not None:
        try:
            text = await _llm_generalize(client, titles, content_all)
            if text:
                title, content = text
            else:
                title, content = _rule_generalize(recs)
        except Exception:  # noqa: BLE001
            title, content = _rule_generalize(recs)
    else:
        title, content = _rule_generalize(recs)

    evidence: list[dict] = []
    for r in recs:
        for ev in r.get("evidence", [])[:2]:
            evidence.append(ev)
    evidence = evidence[:12]
    tags = sorted({t for r in recs for t in r.get("tags", [])})[:10]
    tags = tags or ["经验合并"]

    merged = {
        "id": new_id("exp"),
        "title": title,
        "content": content,
        "tags": tags,
        "kind": "generalization",
        "status": "active" if auto_commit else "draft",
        "confidence": min(0.95, max(0.5, sum(r.get("confidence", 0.5) for r in recs) / len(recs) + 0.05)),
        "scope": "global",
        "agent_key": recs[0].get("agent_key", ""),
        "source_session_id": "",
        "episode_id": "",
        "evidence": evidence,
        "supersedes": [r["id"] for r in recs],
        "replaced_by": "",
        "history": [{"action": "consolidate", "at": now_iso(), "actor": actor,
                     "sources": [r["id"] for r in recs]}],
        "importance": max(r.get("importance", 0) for r in recs),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "source": "consolidate",
    }
    store.save_experience(merged)
    if auto_commit:
        supersede(store, [r["id"] for r in recs], merged["id"],
                  actor=actor, note="consolidation auto commit", graph=graph)
    return {"experience_id": merged["id"], "status": merged["status"],
            "supersedes": merged["supersedes"], "title": merged["title"],
            "content": merged["content"]}


async def _llm_generalize(client, titles: str, content_all: str) -> Optional[tuple[str, str]]:
    from baize.sdk.client import ChatMessage

    system = (
        "你是渗透测试经验库的管理员。下面是一组内容相近的过往经验，"
        "请合并提炼为一条更泛化、可复用的经验：保留共同方法论与踩坑点，"
        "去掉只针对单一目标的具体数值。150 字以内。"
        "只输出 JSON：{\"title\":\"...\",\"content\":\"...\"}"
    )
    prompt = f"【已有经验】\n{titles}\n\n【详细内容】\n{content_all[:6000]}"
    result = await client.complete([
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=prompt),
    ])
    text = (result.content or "") if result else ""
    import json
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        parsed = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None
    title = str(parsed.get("title", "")).strip()
    content = str(parsed.get("content", "")).strip()
    if not title or not content:
        return None
    return title, content


def _rule_generalize(recs: list[dict]) -> tuple[str, str]:
    common_tags = sorted({t for r in recs for t in r.get("tags", [])})
    content = "\n".join(f"· {r.get('title','')}：{r.get('content','')}" for r in recs)
    title = ("同主题经验合并：")
    theme = common_tags[:3]
    title = f"{'/'.join(theme) if theme else '经验'}合并概括"
    return title, content[:2000]
