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
import os
from typing import Iterable, Optional

from .embed import attach_embedding, attach_embedding_sync
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
        attach_embedding_sync(rec)
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
        attach_embedding_sync(rec)
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
        attach_embedding_sync(rec)
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
                      graph=None, max_chars: int = 150) -> Optional[dict]:
    """CONSOLIDATION：把一组同主题经验合并为一条更泛化的新经验。

    新经验默认 status=draft（需用户确认后才取代旧条目）；若 auto_commit=True，
    直接走 supersede() 把旧条目标记为被取代。

    max_chars 是 LLM 提炼的字数上限。手动合并默认 150 字足够——产物是 draft，
    要等人确认才会取代源经验，压过头有人把关。自动合并必须放大（见
    AUTO_CONSOLIDATE_MAX_CHARS），因为源会被立刻置为 superseded 且不再召回。

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
            text = await _llm_generalize(client, titles, content_all, max_chars)
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

    # 合并的意义是提取共性：只保留所有源共有的实体。只在单条出现的实体
    # （如某个具体 IP、某次用过的工具）属于「只针对单一目标的具体数值」，
    # 绑到泛化结论上会让它在不该出现的场景被召回，泛化也就名不副实。
    # 若源之间毫无公共实体（手动合并任意条目时可能出现），退化为并集——
    # 宁可宽，也不能让合并产物彻底进不了实体召回通道。
    keysets = [set(r.get("entity_keys") or []) for r in recs]
    common: set[str] = set.intersection(*keysets) if keysets else set()
    merged_keys = sorted(common) or sorted(
        {k for r in recs for k in (r.get("entity_keys") or [])})

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
        "entity_keys": merged_keys,
        "human_assisted": any(bool(r.get("human_assisted")) for r in recs),
        "target": next((r.get("target") for r in recs if r.get("target")), ""),
        "supersedes": [r["id"] for r in recs],
        "replaced_by": "",
        "history": [{"action": "consolidate", "at": now_iso(), "actor": actor,
                     "sources": [r["id"] for r in recs]}],
        "importance": max(r.get("importance", 0) for r in recs),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "source": "consolidate",
    }
    await attach_embedding(merged)
    store.save_experience(merged)
    if graph is not None and merged["entity_keys"]:
        # 与 learn.py 写入普通经验时一致：补 MENTIONS 边，否则合并产物在
        # 知识图谱里是孤立节点，图谱视图看不到它关联哪类漏洞/目标。
        try:
            graph.link_mentions("experience", merged["id"], merged["entity_keys"])
        except Exception:  # noqa: BLE001
            pass
    if auto_commit:
        supersede(store, [r["id"] for r in recs], merged["id"],
                  actor=actor, note="consolidation auto commit", graph=graph)
    return {"experience_id": merged["id"], "status": merged["status"],
            "supersedes": merged["supersedes"], "title": merged["title"],
            "content": merged["content"]}


async def _llm_generalize(client, titles: str, content_all: str,
                          max_chars: int = 150) -> Optional[tuple[str, str]]:
    """用 LLM 提炼合并结论。

    max_chars 控制压缩力度。手动合并在产物还是 draft 时用 150 字没问题；
    自动合并会立刻让源经验退出召回，压缩过头等于不可逆丢弃信息，故放宽。
    """
    from baize.sdk.client import ChatMessage

    system = (
        "你是渗透测试经验库的管理员。下面是一组内容相近的过往经验，"
        "请合并提炼为一条更泛化、可复用的经验：保留共同方法论与踩坑点，"
        "去掉只针对单一目标的具体数值，但各条经验的关键做法与结论必须保留。"
        f"{max_chars} 字以内。"
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


# ---- 自动合并（Consolidation 调度） ----------------------------------------
# consolidate 此前只挂在 API 端点手动触发，全项目没有任何自动调度：
# supersedes/replaced_by 形同虚设，同类经验只增不演化、无限堆积。
# 这里补上候选发现与自动执行，由「会话学习结束」触发（见 agent.learn_session）。

MIN_CONSOLIDATE_GROUP = 3
# 自动合并的 LLM 字数上限：源经验会被立刻置为 superseded 并退出召回，合并
# 产物是唯一还能被召回的载体，必须能承载各条源经验的关键做法与结论。
AUTO_CONSOLIDATE_MAX_CHARS = 1200


def find_consolidation_candidates(store: MemoryStore,
                                  min_group: int = MIN_CONSOLIDATE_GROUP,
                                  ) -> list[dict]:
    """找出可合并的候选组：共享同一实体（同类漏洞 / 同类目标）且数量达阈值。

    约束：
    - 只在同一 kind 内合并，避免把「方法」与「教训」混成一条；
    - 排除 generalization，避免合并产物再被合并形成套娃；
    - 排除 superseded / invalidated（已是历史版本，不参与再合并）。
    """
    by_key: dict[str, list[dict]] = {}
    for exp in store.list_experiences():
        if exp.get("status") in ("superseded", "invalidated"):
            continue
        if exp.get("kind") == "generalization":
            continue
        for k in exp.get("entity_keys") or []:
            by_key.setdefault(k, []).append(exp)

    cands: list[dict] = []
    for key, recs in by_key.items():
        if len(recs) < min_group:
            continue
        by_kind: dict[str, list[dict]] = {}
        for r in recs:
            by_kind.setdefault(r.get("kind") or "", []).append(r)
        kind, sub = max(by_kind.items(), key=lambda kv: len(kv[1]))
        if len(sub) < min_group:
            continue
        sub.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or "")
        cands.append({"key": key, "kind": kind, "ids": [r["id"] for r in sub]})
    cands.sort(key=lambda c: len(c["ids"]), reverse=True)
    return cands


async def auto_consolidate(store: MemoryStore, *, client=None, graph=None,
                           actor: str = "auto",
                           min_group: int = MIN_CONSOLIDATE_GROUP,
                           max_groups: int = 1) -> list[dict]:
    """自动把达到阈值的同类经验合并为一条更泛化的经验。

    与手动 consolidate 不同，这里必须 auto_commit：否则源经验不会置为
    superseded，下次仍会被选为候选，反复产出内容雷同的草稿，堆积问题原地不动。
    源经验不会被删除——置为 superseded 并保留 replaced_by 谱系，可回溯。

    可用环境变量 BAIZE_MEMORY_AUTO_CONSOLIDATE=0 关闭。
    """
    flag = os.environ.get("BAIZE_MEMORY_AUTO_CONSOLIDATE", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return []
    out: list[dict] = []
    for cand in find_consolidation_candidates(store, min_group)[:max_groups]:
        try:
            res = await consolidate(store, cand["ids"], actor=actor, client=client,
                                    auto_commit=True, graph=graph,
                                    max_chars=AUTO_CONSOLIDATE_MAX_CHARS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动合并失败 key=%s: %s", cand["key"], exc)
            continue
        if res:
            res["group"] = cand["key"]
            out.append(res)
            logger.info("自动合并: 实体=%s 合并 %d 条 -> %s",
                        cand["key"], len(cand["ids"]), res.get("experience_id"))
    return out
