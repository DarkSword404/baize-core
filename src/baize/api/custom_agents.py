"""自定义智能体与自定义管道的持久化管理（独立实现）。

用户可在 Web 界面创建自定义智能体（自定义指令/工具）和自定义
编排管道（多智能体流程）。数据持久化到 ``~/.baize/custom/``。

同时还维护内置资源的黑名单，支持用户删除内置智能体和模板。
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
        self._agents = {}
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
        with self._lock:
            self._load()
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

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        """按名称查找自定义智能体（用于会话中解析智能体）。"""
        with self._lock:
            self._load()
            for a in self._agents.values():
                if a.get("name") == name:
                    return a
        return None


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
        # 支持 nodes/edges (图结构) 也保留 steps 向后兼容
        pipeline = {
            "id": secrets.token_hex(10),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "steps": data.get("steps", data.get("nodes", [])),
            "nodes": data.get("nodes", data.get("steps", [])),
            "edges": data.get("edges", []),
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
            for key in ("name", "description", "steps", "nodes", "edges"):
                if key in data:
                    pipeline[key] = data[key]
            # 同步 nodes ← steps 或 steps ← nodes
            if "nodes" in data and "steps" not in data:
                pipeline["steps"] = data["nodes"]
            if "steps" in data and "nodes" not in data:
                pipeline["nodes"] = data["steps"]
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


# ---------------------------------------------------------------------------
# 内置资源黑名单 — 支持用户删除内置智能体和模板
# ---------------------------------------------------------------------------

class DeletedResourcesStore:
    """管理内置资源的"软删除"黑名单，数据持久化到 ~/.baize/custom/。

    内置智能体和模板代码不可物理删除，但用户可通过 API 将其标记为已删除，
    此后它们在 API 返回列表中不可见，也可以通过重置接口恢复所有。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents_file = CUSTOM_DIR / "deleted_agents.json"
        self._templates_file = CUSTOM_DIR / "deleted_templates.json"
        self._deleted_agents: set[str] = self._load(self._agents_file)
        self._deleted_templates: set[str] = self._load(self._templates_file)

    @staticmethod
    def _load(filepath: Path) -> set[str]:
        try:
            if filepath.exists():
                data = json.loads(filepath.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return set(data)
        except (json.JSONDecodeError, OSError):
            pass
        return set()

    def _save(self, filepath: Path, data: set[str]) -> None:
        CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
        tmp = filepath.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(data), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(filepath)

    # ---- agents ----

    def is_agent_deleted(self, name: str) -> bool:
        with self._lock:
            return name in self._deleted_agents

    def delete_agent(self, name: str) -> bool:
        with self._lock:
            if name in self._deleted_agents:
                return True  # 已删除，幂等
            self._deleted_agents.add(name)
            self._save(self._agents_file, self._deleted_agents)
            return True

    def reset_agents(self) -> int:
        with self._lock:
            count = len(self._deleted_agents)
            self._deleted_agents.clear()
            if self._agents_file.exists():
                self._agents_file.unlink()
            return count

    # ---- templates ----

    def is_template_deleted(self, tid: str) -> bool:
        with self._lock:
            return tid in self._deleted_templates

    def delete_template(self, tid: str) -> bool:
        with self._lock:
            if tid in self._deleted_templates:
                return True
            self._deleted_templates.add(tid)
            self._save(self._templates_file, self._deleted_templates)
            return True

    def reset_templates(self) -> int:
        with self._lock:
            count = len(self._deleted_templates)
            self._deleted_templates.clear()
            if self._templates_file.exists():
                self._templates_file.unlink()
            return count


# 全局单例
_deleted_store: DeletedResourcesStore | None = None


def get_deleted_store() -> DeletedResourcesStore:
    global _deleted_store
    if _deleted_store is None:
        _deleted_store = DeletedResourcesStore()
    return _deleted_store
