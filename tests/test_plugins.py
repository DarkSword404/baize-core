from __future__ import annotations

from types import SimpleNamespace

import pytest

from baize.plugins import PluginContext, PluginEvent, PluginManager
from baize.plugins.asmp import ASMPPlugin, ASMPService


def _event(sid: str, kind: str, payload: dict) -> PluginEvent:
    return PluginEvent(session_id=sid, kind=kind, payload=payload)


def test_asmp_ingests_http_artifacts_into_model(tmp_path) -> None:
    svc = ASMPService(tmp_path / "asmp")
    svc.on_event(_event("s1", "user/message", {"content": "测试 portal 认证入口"}))
    svc.on_event(_event("s1", "tool/call", {
        "name": "http_session_request",
        "arguments": '{"url":"https://example.com/portal"}',
        "call_id": "c1",
    }))
    svc.on_event(_event("s1", "tool/result", {
        "name": "http_session_request",
        "call_id": "c1",
        "output": (
            "HTTP 200\n[自动提取的 Web 证据]\n"
            "## Scripts\n- /static/js/login.js\n"
            "## Forms\n- POST /api/portal/auth/guest/login fields=[guest_uid:text, password:password]\n"
            "## API paths\n- /api/portal/auth/guest/login\n"
        ),
    }))
    snap = svc.get_model_snapshot("s1")
    types = {n["type_id"] for n in snap["nodes"]}
    assert "host" in types
    assert "web_application" in types
    assert "authentication_service" in types
    assert any(r["type_id"] == "exposes" for r in snap["relations"])
    assert snap["evidence"]
    assert snap["hypotheses"]
    assert snap["unknowns"]
    assert snap["model_version"] >= 1
    assert any(str(e.get("raw_artifact_ref", "")).endswith(".json") for e in snap["evidence"])
    brief = svc.brief("s1")
    assert "认证" in brief or "威胁假设" in brief


def test_asmp_tasks_and_hypothesis_transition(tmp_path) -> None:
    svc = ASMPService(tmp_path / "asmp")
    svc.on_event(_event("s1", "tool/call", {
        "name": "http_request",
        "arguments": '{"url":"https://example.com/api/login"}',
        "call_id": "c1",
    }))
    svc.on_event(_event("s1", "tool/result", {
        "name": "http_request",
        "call_id": "c1",
        "output": "HTTP 200\n## Forms\n- POST /api/login fields=[user:text, password:password]\n",
    }))
    tasks = svc.propose_tasks("s1", limit=10)
    assert tasks
    assert tasks[0]["status"] == "proposed"
    assert tasks[0]["required_evidence"]
    hyp = svc.get_hypotheses("s1")[0]
    updated = svc.update_hypothesis(
        "s1", hyp["id"], status="blocked", note="缺少测试身份"
    )
    assert updated["ok"] is True
    assert updated["hypothesis"]["status"] == "blocked"
    # invalid state must be rejected
    rejected = svc.update_hypothesis("s1", hyp["id"], status="not-a-state")
    assert rejected["ok"] is False
    unknown = svc.get_unknowns("s1")[0]
    resolved = svc.update_unknown("s1", unknown["id"], status="resolved", note="已收集证据")
    assert resolved["ok"] is True
    assert resolved["unknown"]["status"] == "resolved"


def test_asmp_replays_session_log(tmp_path) -> None:
    log = tmp_path / "s1.audit.jsonl"
    log.write_text(
        "\n".join([
            '{"kind":"user/message","payload":{"content":"hello"}}',
            '{"kind":"tool/call","payload":{"name":"http_request","arguments":"{\\"url\\":\\"https://example.com/portal\\"}","call_id":"c1"}}',
            '{"kind":"tool/result","payload":{"name":"http_request","output":"HTTP 200\\n## Forms\\n- POST /api/login fields=[user:text, password:password]\\n"}}',
        ]) + "\n",
        encoding="utf-8",
    )
    svc = ASMPService(tmp_path / "asmp")
    assert svc.replay_from_log("s1", log) is True
    snap = svc.get_model_snapshot("s1")
    assert snap["nodes"]
    assert any(n["type_id"] == "authentication_service" for n in snap["nodes"])
    assert svc.replay_from_log("s1", log) is False


def test_asmp_type_validation_and_proposal(tmp_path) -> None:
    svc = ASMPService(tmp_path / "asmp")
    bad = svc.validate_type_definition("s1", {"type_id": "Bad-Type", "display_name": ""})
    assert bad["ok"] is False
    assert bad["errors"]
    good = {
        "type_id": "message_queue",
        "display_name": "消息队列",
        "kind": "node",
        "base_type": "service",
        "attributes": {"protocol": {"type": "string"}},
        "threat_profile": ["trust_boundary"],
    }
    result = svc.propose_type_definition("s1", good, reason="现有类型无法表达消息队列")
    assert result["ok"] is True
    assert result["type"]["status"] == "proposed"
    assert any(t["type_id"] == "message_queue" for t in svc.get_types("s1"))
    registered = svc.register_type("s1", "message_queue")
    assert registered["ok"] is True
    assert registered["type"]["status"] == "registered"


@pytest.mark.asyncio
async def test_asmp_plugin_registers_tools(tmp_path) -> None:
    registered: dict[str, object] = {}

    def _register(name, service):
        registered[name] = service

    ctx = PluginContext(
        data_dir=tmp_path,
        services=SimpleNamespace(register=_register),
        logger=SimpleNamespace(),
    )
    plugin = ASMPPlugin()
    await plugin.start(ctx)
    assert isinstance(registered.get("asmp_service"), ASMPService)
    tools = {t.name for t in plugin.tools("s1")}
    assert {
        "asmp_snapshot", "asmp_query", "asmp_ingest", "asmp_propose_tasks",
        "asmp_update_hypothesis", "asmp_apply_result", "asmp_propose_type",
        "asmp_validate_type", "asmp_changes",
    }.issubset(tools)
    assert "asmp_snapshot" in plugin.context_hint("s1")
    plugin.on_event(_event("s1", "user/message", {"content": "hello"}))
    assert plugin.service is not None
    assert "hello" in plugin.service.brief("s1") or "攻击态势" in plugin.service.brief("s1")


@pytest.mark.asyncio
async def test_plugin_manager_lifecycle_and_event_fanout(tmp_path) -> None:
    class DemoPlugin(ASMPPlugin):
        def __init__(self):
            super().__init__()
            self.events = []

        def on_event(self, event):
            self.events.append(event.kind)

        def tools(self, session_id):
            return []

        def context_hint(self, session_id):
            return "demo hint"

        def ui_manifest(self):
            return {"panel": "demo"}

    pm = PluginManager(tmp_path, services=SimpleNamespace())
    plugin = DemoPlugin()
    assert pm.register(plugin) is True
    await pm.start()
    pm.publish(_event("s1", "tool/result", {}))
    assert plugin.events == ["tool/result"]
    assert pm.context_hints("s1") == ["demo hint"]
    assert pm.plugins()[0]["id"] == "baize.asmp"
    assert pm.ui_manifests() == [{"plugin": "baize.asmp", "panel": "demo"}]
    await pm.stop()
