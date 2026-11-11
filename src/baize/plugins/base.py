"""Harness plugin primitives.

A plugin is not a tool. A tool is one callable function; a plugin owns a
lifecycle, can observe events, contribute tools/UI, and maintain state across
an agent session.

Kernel safety (auth, scope_guard, sandbox, audit) stays outside plugins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class PluginManifest:
    """Static plugin metadata.

    ``permissions`` is intentionally declarative; the host is responsible for
    enforcing it. A plugin must never bypass kernel safety checks.
    """

    id: str
    name: str
    version: str = "0.1.0"
    kind: str = "capability"  # capability | state | ui | toolpack
    description: str = ""
    permissions: tuple[str, ...] = ()
    config_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginEvent:
    """Event delivered to plugins by the harness.

    ``kind`` follows SessionLog vocabulary (tool/call, tool/result,
    user/message, session/start, session/end, turn/start, turn/end, ...).
    """

    session_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "session_log"
    ts: float = 0.0


@dataclass
class PluginContext:
    """Restricted context passed to a plugin on start."""

    data_dir: Path
    services: Any
    logger: Any
    manager: Any = None
    config: dict[str, Any] = field(default_factory=dict)


class Plugin(Protocol):
    """Minimal plugin protocol.

    Implementations can be sync or async for start/stop; ``on_event`` is sync
    because it is called from SessionLog's append-only sink on the request
    path. Heavy work must be queued by the plugin itself.
    """

    manifest: PluginManifest

    async def start(self, ctx: PluginContext) -> None:
        ...

    async def stop(self) -> None:
        ...

    def on_event(self, event: PluginEvent) -> None:
        ...

    def tools(self, session_id: str) -> list[Any]:
        """Return AgentTool instances contributed to a session."""
        return []

    def context_hint(self, session_id: str) -> str:
        """Optional one-line capability hint injected into worker instructions.

        Keep this tiny and non-prescriptive. It is capability discovery, not a
        workflow rule.
        """
        return ""

    def context_block(self, session_id: str) -> str:
        """Optional read-only state block injected into worker instructions.

        Unlike a prompt rule, this is current task state. Plugins should keep it
        compact and must not use it to mandate a workflow.
        """
        return ""

    def ui_manifest(self) -> dict[str, Any]:
        """Optional UI contribution descriptor."""
        return {}


class BasePlugin:
    """Convenience base class with no-op lifecycle methods."""

    manifest = PluginManifest(id="base", name="Base Plugin")

    async def start(self, ctx: PluginContext) -> None:  # noqa: D401
        self.ctx = ctx

    async def stop(self) -> None:
        return None

    def on_event(self, event: PluginEvent) -> None:
        return None

    def tools(self, session_id: str) -> list[Any]:
        return []

    def context_hint(self, session_id: str) -> str:
        return ""

    def ui_manifest(self) -> dict[str, Any]:
        return {}


def optional_manifest(plugin: Any) -> Optional[PluginManifest]:
    manifest = getattr(plugin, "manifest", None)
    return manifest if isinstance(manifest, PluginManifest) else None
