"""Input normalization for ASMP.

Raw tool events and human input are converted into a stable Observation shape.
The normalizer may extract obvious artifacts (URLs/forms/API paths), but it
never promotes an LLM summary into a fact.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from baize.memory.utils import new_id, now_iso


@dataclass
class Observation:
    observation_id: str
    engagement_id: str
    source_kind: str
    source_ref: str
    observed_at: str
    scope_ref: str
    content_kind: str
    data: dict[str, Any] = field(default_factory=dict)
    raw_artifact_ref: str = ""
    human_confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "engagement_id": self.engagement_id,
            "source": {"kind": self.source_kind, "reference": self.source_ref},
            "observed_at": self.observed_at,
            "scope": {"reference": self.scope_ref},
            "content": {"kind": self.content_kind, "data": self.data},
            "raw_artifact_ref": self.raw_artifact_ref,
            "human_confirmed": self.human_confirmed,
        }


def _args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def parse_web_artifacts(output: str) -> dict[str, list[str]]:
    """Extract compact artifacts from the text emitted by HTTP tools."""
    result: dict[str, list[str]] = {"scripts": [], "forms": [], "apis": [], "links": []}
    section = ""
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if line.startswith("## Scripts"):
            section = "scripts"
            continue
        if line.startswith("## Forms"):
            section = "forms"
            continue
        if line.startswith("## API paths"):
            section = "apis"
            continue
        if line.startswith("## Links"):
            section = "links"
            continue
        if line.startswith("## "):
            section = ""
            continue
        if section and line.startswith("- "):
            result[section].append(line[2:].strip())
    # 即使工具没有附带结构化的 ## API paths，也从 JS/HTML 原文中提取
    # fetch(...) 和 /api/... 路径，避免漏掉 subsystems.js 之类的后续入口。
    for match in re.finditer(r"""fetch\(\s*['"]([^'"]+)['"]""", str(output or "")):
        result["apis"].append(match.group(1))
    for match in re.finditer(
        r"(?<![\w])(/(?:api|rest|graphql|v\d+)[A-Za-z0-9_?&=./:{}\-]*)",
        str(output or ""),
    ):
        result["apis"].append(match.group(1))
    for key in result:
        result[key] = list(dict.fromkeys(result[key]))
    return result


def observation_from_tool_result(
    *,
    engagement_id: str,
    name: str,
    output: str,
    arguments: Any,
    call_id: str,
    status: str = "done",
    scope_ref: str = "",
) -> Observation:
    args = _args(arguments)
    artifacts = parse_web_artifacts(output)
    target = (
        args.get("url") or args.get("target") or args.get("host")
        or args.get("domain") or ""
    )
    return Observation(
        observation_id=new_id("obs"),
        engagement_id=engagement_id,
        source_kind="tool_execution",
        source_ref=f"tool:{call_id or name}",
        observed_at=now_iso(),
        scope_ref=scope_ref or engagement_id,
        content_kind="tool_execution",
        data={
            "tool": name,
            "arguments": args,
            "target": target,
            "output": output[:20000],
            "status": status,
            "artifacts": artifacts,
        },
        raw_artifact_ref=f"session:{engagement_id}:{call_id or name}",
        human_confirmed=False,
    )


def observation_from_human(engagement_id: str, content: str) -> Observation:
    return Observation(
        observation_id=new_id("obs"),
        engagement_id=engagement_id,
        source_kind="human",
        source_ref="user_message",
        observed_at=now_iso(),
        scope_ref=engagement_id,
        content_kind="human_input",
        data={"content": content[:5000]},
        human_confirmed=True,
    )


def observation_from_dict(engagement_id: str, payload: dict[str, Any]) -> Observation:
    source = payload.get("source") or {}
    scope = payload.get("scope") or {}
    content = payload.get("content") or {}
    return Observation(
        observation_id=str(payload.get("observation_id") or new_id("obs")),
        engagement_id=str(payload.get("engagement_id") or engagement_id),
        source_kind=str(source.get("kind") or "external"),
        source_ref=str(source.get("reference") or ""),
        observed_at=str(payload.get("observed_at") or now_iso()),
        scope_ref=str(scope.get("reference") or engagement_id),
        content_kind=str(content.get("kind") or "generic"),
        data=dict(content.get("data") or {}),
        raw_artifact_ref=str(payload.get("raw_artifact_ref") or ""),
        human_confirmed=bool(payload.get("human_confirmed")),
    )


def normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def host_from_url(url: str) -> str:
    text = normalize_url(url)
    m = re.match(r"https?://([^/:]+)", text, flags=re.IGNORECASE)
    return m.group(1).lower() if m else ""
