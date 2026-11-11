"""Minimal harness plugin host.

Responsibilities:
- register/start/stop plugins
- publish SessionLog events
- expose plugin-contributed AgentTools and UI descriptors
- keep plugin failures isolated from the agent loop

This is intentionally small. Discovery from disk/MCP can be layered on later.
"""
from __future__ import annotations

import inspect
import logging
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from .base import Plugin, PluginContext, PluginEvent, optional_manifest

logger = logging.getLogger("baize.plugins")


class PluginManager:
    def __init__(self, data_dir: Path | str, services: Any = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.services = services
        self._plugins: dict[str, Plugin] = {}
        self._started = False
        self._lock = threading.RLock()

    # ---- lifecycle -----------------------------------------------------
    def register(self, plugin: Plugin) -> bool:
        manifest = optional_manifest(plugin)
        if manifest is None:
            raise ValueError("plugin must expose PluginManifest as .manifest")
        with self._lock:
            if manifest.id in self._plugins:
                logger.warning("插件已存在，忽略重复注册: %s", manifest.id)
                return False
            self._plugins[manifest.id] = plugin
            return True

    async def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            plugins = list(self._plugins.values())
        for plugin in plugins:
            manifest = optional_manifest(plugin)
            ctx = PluginContext(
                data_dir=self.data_dir,
                services=self.services,
                logger=logger,
                manager=self,
                config={},
            )
            try:
                result = plugin.start(ctx)
                if inspect.isawaitable(result):
                    await result
                logger.info("插件已启动: %s v%s", manifest.id, manifest.version)
            except Exception:  # noqa: BLE001
                logger.exception("插件启动失败: %s", getattr(manifest, "id", "?"))

    async def stop(self) -> None:
        with self._lock:
            plugins = list(self._plugins.values())
            self._started = False
        for plugin in plugins:
            manifest = optional_manifest(plugin)
            try:
                result = plugin.stop()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.warning("插件停止失败: %s", getattr(manifest, "id", "?"), exc_info=True)

    # ---- events --------------------------------------------------------
    def publish(self, event: PluginEvent) -> None:
        """Publish an event synchronously; plugin errors never break the agent."""
        with self._lock:
            plugins = list(self._plugins.values())
        for plugin in plugins:
            try:
                plugin.on_event(event)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "插件事件处理失败: %s kind=%s session=%s",
                    getattr(getattr(plugin, "manifest", None), "id", "?"),
                    event.kind,
                    event.session_id,
                    exc_info=True,
                )

    # ---- contributions -------------------------------------------------
    def plugins(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for plugin in self._plugins.values():
                manifest = optional_manifest(plugin)
                if manifest is None:
                    continue
                out.append({
                    "id": manifest.id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "kind": manifest.kind,
                    "description": manifest.description,
                    "permissions": list(manifest.permissions),
                })
            return out

    def agent_tools(self, session_id: str, *, plugin_ids: Optional[Iterable[str]] = None) -> list[Any]:
        """Collect tools contributed by plugins for a session."""
        allowed = set(plugin_ids) if plugin_ids else None
        with self._lock:
            plugins = list(self._plugins.values())
        tools: list[Any] = []
        for plugin in plugins:
            manifest = optional_manifest(plugin)
            if manifest is None:
                continue
            if allowed is not None and manifest.id not in allowed:
                continue
            try:
                tools.extend(plugin.tools(session_id))
            except Exception:  # noqa: BLE001
                logger.warning("插件贡献工具失败: %s", manifest.id, exc_info=True)
        return tools

    def context_blocks(self, session_id: str) -> list[str]:
        """Collect optional read-only state blocks from plugins."""
        with self._lock:
            plugins = list(self._plugins.values())
        blocks: list[str] = []
        for plugin in plugins:
            try:
                block = plugin.context_block(session_id)
                if isinstance(block, str) and block.strip():
                    blocks.append(block.strip())
            except Exception:  # noqa: BLE001
                logger.warning("插件上下文块失败", exc_info=True)
        return blocks

    def context_hints(self, session_id: str) -> list[str]:
        """Collect tiny capability hints from plugins."""
        with self._lock:
            plugins = list(self._plugins.values())
        hints: list[str] = []
        for plugin in plugins:
            try:
                hint = plugin.context_hint(session_id)
                if isinstance(hint, str) and hint.strip():
                    hints.append(hint.strip())
            except Exception:  # noqa: BLE001
                logger.warning("插件上下文提示失败", exc_info=True)
        return hints

    def ui_manifests(self) -> list[dict[str, Any]]:
        with self._lock:
            plugins = list(self._plugins.values())
        out: list[dict[str, Any]] = []
        for plugin in plugins:
            manifest = optional_manifest(plugin)
            if manifest is None:
                continue
            try:
                ui = plugin.ui_manifest()
                if ui:
                    out.append({"plugin": manifest.id, **ui})
            except Exception:  # noqa: BLE001
                logger.warning("插件 UI 描述失败: %s", manifest.id, exc_info=True)
        return out

    def get(self, plugin_id: str) -> Optional[Plugin]:
        with self._lock:
            return self._plugins.get(plugin_id)
