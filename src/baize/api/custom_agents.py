"""自定义智能体与自定义管道的持久化管理（独立实现）。

用户可在 Web 界面创建自定义智能体（自定义指令/工具）和自定义
编排管道（多智能体流程）。数据持久化到 ``~/.baize/custom/``。
"""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from baize.config import DEFAULT_BAIZE_DIR

CUSTOM_DIR = DEFAULT_BAIZE_DIR / "custom"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CustomAgentStore:
    """自定义智能体存储。"""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or (CUSTOM_DIR / "agents")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._agents: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        for f in self._dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                self._agents[data["id"]] = data
            except (json.JSONDecodeError, OSError, KeyError):
                continue

    def _save(self, agent: dict[str, Any]) -> None:
        f = self._dir / f"{agent['id']}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(agent, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._agents.values(), key=lambda a: a["created_at"])

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        agent = {
            "id": secrets.token_hex(10),
            "name": data.get("name", ""),
            "display_name": data.get("display_name", data.get("name", "")),
            "description": data.get("description", ""),
            "instructions": data.get("instructions", ""),
            "model": data.get("model", ""),
            "tools": data.get("tools", []),
            "created_at": now,
            "updated_at": now,
            "is_custom": True,
        }
        with self._lock:
            self._agents[agent["id"]] = agent
            self._save(agent)
        return agent

    def update(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return None
            for key in ("name", "display_name", "description", "instructions", "model", "tools"):
                if key in data:
                    agent[key] = data[key]
            agent["updated_at"] = _now()
            self._save(agent)
            return agent

    def delete(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id not in self._agents:
                return False
            del self._agents[agent_id]
            f = self._dir / f"{agent_id}.json"
            if f.exists():
                f.unlink()
            return True


class CustomPipelineStore:
    """自定义管道存储。"""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or (CUSTOM_DIR / "pipelines")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._pipelines: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        for f in self._dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                self._pipelines[data["id"]] = data
            except (json.JSONDecodeError, OSError, KeyError):
                continue

    def _save(self, pipeline: dict[str, Any]) -> None:
        f = self._dir / f"{pipeline['id']}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(pipeline, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._pipelines.values(), key=lambda p: p["created_at"])

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        pipeline = {
            "id": secrets.token_hex(10),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "steps": data.get("steps", []),
            "created_at": now,
            "updated_at": now,
            "is_custom": True,
        }
        with self._lock:
            self._pipelines[pipeline["id"]] = pipeline
            self._save(pipeline)
        return pipeline

    def update(self, pipeline_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            pipeline = self._pipelines.get(pipeline_id)
            if pipeline is None:
                return None
            for key in ("name", "description", "steps"):
                if key in data:
                    pipeline[key] = data[key]
            pipeline["updated_at"] = _now()
            self._save(pipeline)
            return pipeline

    def delete(self, pipeline_id: str) -> bool:
        with self._lock:
            if pipeline_id not in self._pipelines:
                return False
            del self._pipelines[pipeline_id]
            f = self._dir / f"{pipeline_id}.json"
            if f.exists():
                f.unlink()
            return True
