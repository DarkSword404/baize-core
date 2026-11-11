"""ASMP V1.0 core data objects.

Objects are deliberately separate:
- Node / Relation: environment model
- Evidence: observed material
- ThreatHypothesis: unverified security judgement
- Unknown: explicit knowledge gap
- ExplorationTask: proposed work, never an execution grant
- TypeDefinition / ModelChange: schema and audit
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from baize.memory.utils import new_id, now_iso

ATTRIBUTE_STATES = ("observed", "inferred", "unknown", "conflicted", "not_applicable")
NODE_STATUSES = ("discovered", "partially_observed", "modeled", "stale", "merged", "archived")
RELATION_STATUSES = ("observed", "inferred", "hypothesized", "disproven", "stale")
EVIDENCE_STATUSES = ("pending_review", "accepted", "rejected", "superseded")
HYPOTHESIS_STATUSES = (
    "candidate", "ready", "in_progress", "confirmed", "refuted",
    "blocked", "inconclusive", "closed",
)
TASK_STATUSES = ("proposed", "accepted", "rejected", "running", "completed", "failed", "cancelled")
TYPE_STATUSES = ("builtin", "proposed", "validated", "registered", "deprecated", "rejected")


def now() -> str:
    return now_iso()


@dataclass
class Node:
    id: str
    engagement_id: str
    type_id: str
    name: str
    summary: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    status: str = "discovered"
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Node":
        return cls(**{k: d[k] for k in cls.__annotations__ if k in d})

    @classmethod
    def new(cls, engagement_id: str, type_id: str, name: str, **kw: Any) -> "Node":
        return cls(id=new_id("node"), engagement_id=engagement_id,
                   type_id=type_id, name=name, **kw)


@dataclass
class Relation:
    id: str
    engagement_id: str
    source_id: str
    target_id: str
    type_id: str
    direction: str = "directed"
    conditions: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.5
    status: str = "observed"
    validity: dict[str, Any] = field(default_factory=lambda: {"from": now(), "to": None})
    version: int = 1
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Relation":
        return cls(**{k: d[k] for k in cls.__annotations__ if k in d})

    @classmethod
    def new(cls, engagement_id: str, source_id: str, target_id: str,
            type_id: str, **kw: Any) -> "Relation":
        return cls(id=new_id("rel"), engagement_id=engagement_id,
                   source_id=source_id, target_id=target_id,
                   type_id=type_id, **kw)


@dataclass
class Evidence:
    id: str
    engagement_id: str
    source_kind: str
    source_ref: str
    subject_refs: list[str] = field(default_factory=list)
    observation: str = ""
    raw_artifact_ref: str = ""
    observed_at: str = field(default_factory=now)
    scope_ref: str = ""
    reliability: str = "direct_observation"
    status: str = "accepted"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(**{k: d[k] for k in cls.__annotations__ if k in d})


@dataclass
class ThreatHypothesis:
    id: str
    engagement_id: str
    title: str
    category: str
    target_refs: list[str] = field(default_factory=list)
    claim: str = ""
    supporting_evidence_refs: list[str] = field(default_factory=list)
    contradicting_evidence_refs: list[str] = field(default_factory=list)
    preconditions: list[dict[str, Any]] = field(default_factory=list)
    impact: dict[str, Any] = field(default_factory=lambda: {"level": "medium", "rationale": ""})
    confidence: float = 0.4
    status: str = "candidate"
    next_action_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    version: int = 1
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ThreatHypothesis":
        return cls(**{k: d[k] for k in cls.__annotations__ if k in d})


@dataclass
class Unknown:
    id: str
    engagement_id: str
    target_refs: list[str] = field(default_factory=list)
    attribute: str = ""
    reason: str = ""
    affects: list[str] = field(default_factory=list)
    verifiable: bool = True
    suggested_evidence: list[str] = field(default_factory=list)
    priority: float = 0.5
    status: str = "open"
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Unknown":
        return cls(**{k: d[k] for k in cls.__annotations__ if k in d})


@dataclass
class ExplorationTask:
    id: str
    engagement_id: str
    objective: str
    target_refs: list[str] = field(default_factory=list)
    hypothesis_refs: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    risk_level: str = "low"
    estimated_cost: int = 2
    priority: float = 0.5
    status: str = "proposed"
    stop_conditions: list[str] = field(default_factory=lambda: [
        "超出授权范围", "触发预设安全停止条件",
    ])
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExplorationTask":
        return cls(**{k: d[k] for k in cls.__annotations__ if k in d})


@dataclass
class ModelChange:
    id: str
    engagement_id: str
    operation: str
    object_type: str
    object_id: str
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None
    reason: str = ""
    actor: str = "plugin"
    created_at: str = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class TypeDefinition:
    type_id: str
    kind: str = "node"  # node | relation
    display_name: str = ""
    schema_version: int = 1
    base_type: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    threat_profile: list[str] = field(default_factory=list)
    analysis_version: int = 1
    status: str = "builtin"
    description: str = ""
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    version: int = 1
    proposal_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TypeDefinition":
        return cls(**{k: d[k] for k in cls.__annotations__ if k in d})
