"""memory 子系统统一数据模型。

直接对应外部设计规格中的记忆类型与生命周期：
- EpisodeRecord    ：Episodic Memory（一次任务 + 完整执行轨迹）
- ExperienceRecord ：Experience Memory（经验，自然语言为核心存储形式）
- FactRecord       ：Semantic Memory（结构化语义事实，带时间有效窗）
- Entity / Relation：Knowledge Graph（实体、关系及时间状态）
- EvidenceRef      ：经验与原始执行证据之间的链接（可回溯到工具调用/消息）

状态约定：
- 内容物（experience / fact / relation）状态机：
      draft（待确认）→ active → superseded（被取代，指向 replaced_by）
                              └→ invalidated（失效/推翻）
- episode 只追加不修改（原始轨迹），以 status 标记任务结局。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .utils import now_iso

# ---- 状态机常量 ----------------------------------------------------------
CONTENT_STATUSES = ("draft", "active", "superseded", "invalidated")
EPISODE_STATUSES = ("success", "failed", "interrupted", "discarded")
REL_STATUSES = ("active", "superseded", "invalidated")

REL_SUPPORTS = "SUPPORTS"        # 经验支持另一条经验/论断
REL_CONTRADICTS = "CONTRADICTS"  # 经验反驳另一条
REL_DERIVED_FROM = "DERIVED_FROM"
REL_SUPERSEDES = "SUPERSEDES"
REL_MENTIONS = "MENTIONS"        # (episode/experience) → entity
REL_REFUTES = "REFUTES"          # 事实对经验反证
REL_EVIDENCE_OF = "EVIDENCE_OF"  # evidence episode → experience


@dataclass
class EvidenceRef:
    """经验对应的原始执行证据链接。

    kind ∈ task/decision/tool/observation/result：指向 Episode 内哪一段；
    ref 是该段在该 Episode 内的内部编号（如 tool_call 序号）；excerpt 冗余快照，
    即使 Episode 原始文件被裁剪也能保留证据可读片段。
    """
    episode_id: str
    kind: str = "result"
    ref: str = ""
    excerpt: str = ""
    at: str = ""

    def to_dict(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "kind": self.kind,
            "ref": self.ref,
            "excerpt": self.excerpt,
            "at": self.at or now_iso(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceRef":
        return cls(
            episode_id=d.get("episode_id", ""),
            kind=d.get("kind", "result"),
            ref=str(d.get("ref", "")),
            excerpt=d.get("excerpt", ""),
            at=d.get("at", ""),
        )


@dataclass
class EpisodeRecord:
    """一次任务的完整执行轨迹（Episodic Memory）。

    task/target/起止时间/动作/工具调用（参数+输出）/观察/结论/结果一应俱全；
    steps 以时序数组保留 Agent action 与 tool call 顺序，作为最原始证据。
    """
    id: str
    task: str = ""
    target: str = ""
    agent_key: str = ""
    session_id: str = ""
    start_at: str = ""
    end_at: str = ""
    status: str = "success"          # EPISODE_STATUSES
    steps: list[dict] = field(default_factory=list)  # 时序：动作/决策/工具调用/观察
    result_text: str = ""
    error_text: str = ""
    entity_keys: list[str] = field(default_factory=list)
    summary: str = ""
    messages_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task": self.task,
            "target": self.target,
            "agent_key": self.agent_key,
            "session_id": self.session_id,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "status": self.status,
            "steps": self.steps,
            "result_text": self.result_text,
            "error_text": self.error_text,
            "entity_keys": list(self.entity_keys),
            "summary": self.summary,
            "messages_count": self.messages_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EpisodeRecord":
        return cls(
            id=d.get("id", ""),
            task=d.get("task", ""),
            target=d.get("target", ""),
            agent_key=d.get("agent_key", ""),
            session_id=d.get("session_id", ""),
            start_at=d.get("start_at", ""),
            end_at=d.get("end_at", ""),
            status=d.get("status", "success"),
            steps=list(d.get("steps", [])),
            result_text=d.get("result_text", ""),
            error_text=d.get("error_text", ""),
            entity_keys=list(d.get("entity_keys", [])),
            summary=d.get("summary", ""),
            messages_count=int(d.get("messages_count", 0)),
        )


@dataclass
class ExperienceRecord:
    """经验条目（Experience Memory）。

    content 始终以自然语言文本为核心存储形式；agent 在检索后自行判断是否参考，
    系统绝不生成可执行 Skill。
    evidence 指向原始 Episode 片段；history 记录每次 REVISE 的增量；
    supersedes/replaced_by 承载「更泛化/更新版本取代旧经验」的谱系。
    """
    id: str
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    kind: str = "method"            # method | lesson | intel | generalization
    status: str = "draft"           # CONTENT_STATUSES
    confidence: float = 0.5
    scope: str = "global"           # global | agent:<key>
    agent_key: str = ""
    source_session_id: str = ""
    episode_id: str = ""
    evidence: list[dict] = field(default_factory=list)   # EvidenceRef.to_dict()
    supersedes: list[str] = field(default_factory=list)
    replaced_by: str = ""
    history: list[dict] = field(default_factory=list)
    importance: int = 0
    created_at: str = ""
    updated_at: str = ""
    source: str = ""                # 何种入口创建：agent | api | consolidate

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "tags": list(self.tags),
            "kind": self.kind,
            "status": self.status,
            "confidence": self.confidence,
            "scope": self.scope,
            "agent_key": self.agent_key,
            "source_session_id": self.source_session_id,
            "episode_id": self.episode_id,
            "evidence": list(self.evidence),
            "supersedes": list(self.supersedes),
            "replaced_by": self.replaced_by,
            "history": list(self.history),
            "importance": int(self.importance),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExperienceRecord":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            content=d.get("content", ""),
            tags=list(d.get("tags", [])),
            kind=d.get("kind", "method"),
            status=d.get("status", "draft"),
            confidence=float(d.get("confidence", 0.5)),
            scope=d.get("scope", "global"),
            agent_key=d.get("agent_key", ""),
            source_session_id=d.get("source_session_id", ""),
            episode_id=d.get("episode_id", ""),
            evidence=list(d.get("evidence", [])),
            supersedes=list(d.get("supersedes", [])),
            replaced_by=d.get("replaced_by", ""),
            history=list(d.get("history", [])),
            importance=int(d.get("importance", 0)),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            source=d.get("source", ""),
        )


@dataclass
class FactRecord:
    """结构化语义事实（Semantic Memory）。

    尽量映射为 (subject) -rel_type-> (object) 图边；statement 是人类可读自然语言。
    valid_from/invalid_at 构成时间有效窗：环境变化后旧事实 invalid_at 而不删除，
    保留其「曾经为真」的历史语义，查询按时间窗取当前有效版本。
    """
    id: str
    statement: str = ""
    subject: str = ""               # entity key（可为空）
    rel_type: str = ""
    object: str = ""                # entity key 或文本值（可为空）
    status: str = "active"          # CONTENT_STATUSES
    confidence: float = 0.6
    valid_from: str = ""
    invalid_at: str = ""
    sources: list[dict] = field(default_factory=list)  # [{kind, id}] episode/experience
    agent_key: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "statement": self.statement,
            "subject": self.subject,
            "rel_type": self.rel_type,
            "object": self.object,
            "status": self.status,
            "confidence": self.confidence,
            "valid_from": self.valid_from,
            "invalid_at": self.invalid_at,
            "sources": list(self.sources),
            "agent_key": self.agent_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FactRecord":
        return cls(
            id=d.get("id", ""),
            statement=d.get("statement", ""),
            subject=d.get("subject", ""),
            rel_type=d.get("rel_type", ""),
            object=d.get("object", ""),
            status=d.get("status", "active"),
            confidence=float(d.get("confidence", 0.6)),
            valid_from=d.get("valid_from", ""),
            invalid_at=d.get("invalid_at", ""),
            sources=list(d.get("sources", [])),
            agent_key=d.get("agent_key", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


# ---- 实体 / 关系（KG 边）约定 ----------------------------------------------
# 实体与关系以 dict 形式存储（键固定），以下为构造/校验辅助。

def new_entity(key: str, kind: str, label: str = "", aliases: list[str] | None = None,
               props: dict | None = None, ts: str = "") -> dict:
    ts = ts or now_iso()
    return {
        "key": key,
        "kind": kind,
        "label": label or key,
        "aliases": list(aliases or []),
        "properties": dict(props or {}),
        "first_seen": ts,
        "last_seen": ts,
    }


def new_relation(rid: str, rel_type: str, source: str, target: str,
                 provenance: dict | None = None, valid_from: str = "",
                 props: dict | None = None, ts: str = "") -> dict:
    """关系边：时间有效窗 + 来源 provenance（可回溯证据）。

    invalid_at 置空即当前有效；作废时填 invalid_at（时间知识图谱核心语义）。
    """
    ts = ts or now_iso()
    return {
        "id": rid,
        "type": rel_type,
        "source": source,
        "target": target,
        "status": "active",
        "valid_from": valid_from or ts,
        "invalid_at": "",
        "provenance": dict(provenance or {"episode_id": "", "at": ts}),
        "properties": dict(props or {}),
        "first_seen": ts,
        "last_seen": ts,
        "occurrences": 1,
    }


def attach_evidence(episode_id: str, kind: str = "result", ref: str = "",
                    excerpt_text: str = "") -> dict:
    return EvidenceRef(episode_id=episode_id, kind=kind, ref=ref,
                       excerpt=excerpt_text).to_dict()
