"""核心回归测试（最小集）。

覆盖本次修复的关键不变量：
- 模型密钥只以掩码形式回显；
- 流水线恢复校验兼容"嵌套节点记录"旧数据；
- 自定义工具创建前校验 handler，测试沙箱能真正执行脚本；
- 审计日志脱敏、哈希链、篡改检测与保留策略。
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from baize.api.app import _backfill_tool_trace_from_log, _is_masked_key, _mask_api_key
from baize.audit import AuditLogger, fingerprint_secret, redact, sanitize_url
from baize.config import ServerConfig
from baize.pentest.blackboard import Blackboard
from baize.pentest.conversation_orchestrator import (
    ConversationOrchestrator,
    _REASON_SYSTEM_PROMPT_BASE,
    _ensure_scope_extracted,
    _is_placeholder_goal,
)
from baize.pentest.scoring import intent_priority
from baize.sdk.agent import _NON_ACTION_TOOL_NAMES
from baize.sdk.session_log import SessionLog
from baize.memory.agent_tools import build_memory_search_tool
from baize.memory.embed import Embedder, embedder, reset_embedder
from baize.memory.retrieve import MemoryRetriever, _business_weight, _parse_iso
from baize.memory.service import MemoryService
from baize.memory.store import MemoryStore
from baize.tools.extended import _http_request
from baize.tools.security_tools import _resolve_wordlist
from baize.tools.session_tools import _render_response
from baize.tools.security_tools_extra import extract_web_artifacts
from baize.orchestration.node_types import PipelineDefinition, PipelineNode
from baize.orchestration.nodes.datatransformer import DataTransformerNodeExecutor
from baize.orchestration.run_store import RunRecord
from baize.orchestration.runner import PipelineRunner
from baize.tools.custom_tools import CustomToolStore, registry
from baize.tools.custom_tools import test_custom_tool as run_custom_tool_test


def _pipeline_with_node(node_id: str = "analyze") -> PipelineDefinition:
    return PipelineDefinition(
        id="p_test",
        name="test",
        nodes=[PipelineNode(id=node_id, type="agent")],
    )


# ---------------------------------------------------------------- 密钥掩码

def test_mask_api_key_hides_secret() -> None:
    masked = _mask_api_key("sk-1234567890abcdef")
    assert masked == "****cdef"
    assert "1234567890" not in masked


def test_mask_api_key_empty_and_short() -> None:
    assert _mask_api_key("") == ""
    assert _mask_api_key("short") == "********"


def test_is_masked_key() -> None:
    assert _is_masked_key("********")
    assert _is_masked_key("****abcd")
    assert not _is_masked_key("sk-real-key")


def test_pipelines_feature_flag_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("BAIZE_ENABLE_PIPELINES", raising=False)
    assert ServerConfig().enable_pipelines is False
    monkeypatch.setenv("BAIZE_ENABLE_PIPELINES", "1")
    assert ServerConfig().enable_pipelines is True
    monkeypatch.setenv("BAIZE_ENABLE_PIPELINES", "0")
    assert ServerConfig().enable_pipelines is False


# ---------------------------------------------------------------- 审计日志

def test_audit_hash_chain_and_redaction(tmp_path) -> None:
    audit = AuditLogger(tmp_path)
    first = audit.record(
        "tool.call",
        actor_type="agent",
        actor_id="session:s1",
        session_id="s1",
        resource_type="tool",
        resource_id="http_request",
        detail={"password": "hunter2", "url": "https://example.com"},
    )
    second = audit.record(
        "tool.result",
        actor_type="agent",
        actor_id="session:s1",
        session_id="s1",
        resource_type="tool",
        resource_id="http_request",
        status="denied",
        detail={"output": "ok"},
    )
    # 密钥只保留指纹，不落明文
    assert first["detail"]["password"] == fingerprint_secret("hunter2")
    assert "hunter2" not in str(first["detail"])
    # 哈希链
    assert second["prev_hash"] == first["hash"]
    assert audit.verify()["ok"] is True
    assert audit.query(action="tool.*")["total"] == 2
    assert audit.query(session_id="s1")["total"] == 2
    assert audit.stats()["total"] == 2


def test_audit_sanitize_url_strips_credentials() -> None:
    assert sanitize_url("https://user:pass@example.com/v1") == "https://***@example.com/v1"
    assert sanitize_url("http://example.com/v1") == "http://example.com/v1"


def test_audit_redacts_secrets_inside_json_strings() -> None:
    redacted = redact({"arguments": '{"password":"hunter2","url":"https://example.com"}'})
    assert "hunter2" not in redacted["arguments"]
    assert fingerprint_secret("hunter2") in redacted["arguments"]


def test_audit_verify_detects_tampering(tmp_path) -> None:
    audit = AuditLogger(tmp_path)
    event = audit.record("session.start", session_id="s1", detail={"goal": "x"})
    conn = sqlite3.connect(tmp_path / "audit.db")
    try:
        conn.execute(
            "UPDATE audit_events SET detail = ? WHERE event_id = ?",
            ('{"goal":"tampered"}', event["event_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    result = audit.verify()
    assert result["ok"] is False
    assert result["broken_at"] == event["event_id"]


def test_audit_retention_preserves_chain(tmp_path) -> None:
    audit = AuditLogger(tmp_path)
    audit.record("session.start", session_id="s1")
    audit.record("session.end", session_id="s1")
    deleted = audit.purge_before(9_999_999_999)
    assert deleted == 2
    # 清理动作本身会作为新事件写入，且剩余链从锚点继续可校验
    result = audit.verify()
    assert result["ok"] is True
    assert result["total"] == 1
    assert audit.query(action="audit.purge")["total"] == 1


def test_session_log_forwards_events_to_audit_sink() -> None:
    forwarded: list[tuple[str, dict]] = []
    log = SessionLog(session_id="s1", audit_sink=lambda kind, payload: forwarded.append((kind, payload)))
    log.append("user/message", content="hello")
    assert forwarded == [("user/message", {"content": "hello"})]


def test_backfill_tool_trace_from_old_session_log(tmp_path) -> None:
    """旧会话只存 reasoning/assistant 时，应从 .audit.jsonl 补回工具轨迹。"""
    session_id = "s_old"
    log = SessionLog(session_id=session_id, path=str(tmp_path / f"{session_id}.audit.jsonl"))
    log.append("tool/call", name="http_request", arguments='{"url":"https://example.com"}', call_id="c1")
    log.append("tool/result", name="http_request", output="HTTP 200", call_id="c1")

    app = SimpleNamespace(state=SimpleNamespace(session_log_dir=str(tmp_path)))
    history = [
        {"role": "user", "content": "test"},
        {"role": "assistant", "content": "done"},
    ]
    enriched = _backfill_tool_trace_from_log(app, session_id, history)
    assert [m.get("type") for m in enriched] == [
        None, "function_call", "function_call_output", None,
    ]
    assert enriched[1]["name"] == "http_request"
    assert enriched[2]["output"] == "HTTP 200"

    # 新会话已有工具轨迹时不得重复补
    unchanged = _backfill_tool_trace_from_log(app, session_id, enriched)
    assert unchanged is enriched


def test_reset_embedder_discards_cached_instance() -> None:
    before = embedder()
    reset_embedder()
    after = embedder()
    assert before is not after


def test_embedding_auto_disabled_without_explicit_model(monkeypatch) -> None:
    llm = SimpleNamespace(base_url="https://example.com/v1", api_key="k", embedding_model="")
    monkeypatch.setattr("baize.memory.embed._llm_model_config", lambda: llm)
    monkeypatch.delenv("BAIZE_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("BAIZE_EMBEDDING_ENABLED", raising=False)
    assert Embedder().enabled() is False


def test_embedding_can_be_forced_on_or_off(monkeypatch) -> None:
    llm = SimpleNamespace(base_url="https://example.com/v1", api_key="k", embedding_model="")
    monkeypatch.setattr("baize.memory.embed._llm_model_config", lambda: llm)
    monkeypatch.setenv("BAIZE_EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv("BAIZE_EMBEDDING_ENABLED", "1")
    assert Embedder().enabled() is True
    monkeypatch.setenv("BAIZE_EMBEDDING_ENABLED", "0")
    assert Embedder().enabled() is False


def test_gobuster_wordlist_fallback(tmp_path, monkeypatch) -> None:
    existing = tmp_path / "words.txt"
    existing.write_text("admin\n", encoding="utf-8")
    path, note = _resolve_wordlist(str(existing))
    assert path == str(existing)
    assert note == ""

    monkeypatch.setattr(
        "baize.tools.security_tools._COMMON_WORDLIST_PATHS", ()
    )
    fallback, note = _resolve_wordlist(str(tmp_path / "missing.txt"))
    from pathlib import Path
    assert Path(fallback).is_file()
    assert "内置最小字典" in note


def test_http_request_appends_html_artifact_summary(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html", "server": "nginx"}
        text = (
            '<html><head><script src="/static/js/login.js"></script></head>'
            '<body><form action="/api/portal/auth/guest/login" method="post">'
            '<input name="guest_uid" type="text" /></form></body></html>'
        )

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("baize.tools.extended.httpx.Client", FakeClient)
    out = _http_request("https://example.com")
    assert "HTTP 200" in out
    assert "自动提取的 Web 证据" in out
    assert "/static/js/login.js" in out
    assert "/api/portal/auth/guest/login" in out

    class FakeJsResponse(FakeResponse):
        headers = {"content-type": "text/javascript", "server": "nginx"}
        text = "fetch('/api/portal/subsystems'); fetch('/api/portal/auth/guest/login');"

    class FakeJsClient(FakeClient):
        def request(self, *args, **kwargs):
            return FakeJsResponse()

    monkeypatch.setattr("baize.tools.extended.httpx.Client", FakeJsClient)
    js_out = _http_request("https://example.com/static/js/login.js")
    assert "自动提取的 Web 证据" in js_out
    assert "/api/portal/subsystems" in js_out
    assert "/api/portal/auth/guest/login" in js_out


def test_session_response_appends_artifact_summary() -> None:
    class FakeHeaders(dict):
        def get_list(self, key):
            value = self.get(key)
            return [value] if value else []

    class FakeResp:
        status_code = 200
        headers = FakeHeaders({"content-type": "text/html", "server": "nginx"})
        text = (
            '<html><head><script src="/static/js/login.js"></script></head>'
            '<body><form action="/api/portal/auth/guest/login" method="post">'
            '<input name="guest_uid" type="text" /></form></body></html>'
        )

    out = _render_response(
        session_name="s", sess={"cookies": {}, "headers": {}, "auth": ""},
        resp=FakeResp(), final_url="https://example.com/portal",
        final_method="GET", hops=[], max_body=80,
    )
    assert "已截断" in out
    assert "自动提取的 Web 证据" in out
    assert "/static/js/login.js" in out
    assert "/api/portal/auth/guest/login" in out


def test_extract_web_artifacts_from_html() -> None:
    html = """
    <html><head><script src="/static/js/login.js"></script></head>
    <body>
      <form action="/api/portal/auth/guest/login" method="post">
        <input name="guest_uid" type="text" />
        <input name="password" type="password" />
      </form>
      <a href="/portal">portal</a>
      <script>fetch('/api/portal/subsystems')</script>
    </body></html>
    """
    out = extract_web_artifacts(html, "https://example.com")
    assert "/static/js/login.js" in out
    assert "POST https://example.com/api/portal/auth/guest/login" in out
    assert "guest_uid:text" in out
    assert "/api/portal/subsystems" in out


def test_memory_business_weight_uses_confidence_and_recency() -> None:
    fresh_high = {
        "confidence": 0.9,
        "created_at": "2026-10-20T00:00:00+00:00",
        "updated_at": "2026-10-20T00:00:00+00:00",
    }
    stale_low = {
        "confidence": 0.2,
        "created_at": "2020-01-01T00:00:00+00:00",
        "updated_at": "2020-01-01T00:00:00+00:00",
    }
    assert _business_weight(fresh_high) > _business_weight(stale_low)
    assert _parse_iso("2026-10-20T00:00:00Z") is not None
    assert _parse_iso("not-a-date") is None


def test_memory_stats_exposes_mode_and_embedding(tmp_path) -> None:
    svc = MemoryService(root=tmp_path)
    stats = svc.stats()
    assert stats["mode"] in ("off", "tool", "brief", "auto")
    assert "embedding_enabled" in stats


def test_retrieval_skips_expired_and_future_facts(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    base = {
        "statement": "sql injection test target",
        "status": "active",
        "confidence": 0.9,
        "sources": [],
    }
    store.save_fact({**base, "id": "f_valid"})
    store.save_fact({
        **base, "id": "f_expired", "invalid_at": "2020-01-01T00:00:00+00:00",
    })
    store.save_fact({
        **base, "id": "f_future", "valid_from": "2099-01-01T00:00:00+00:00",
    })
    retriever = MemoryRetriever(store)
    data = retriever.search("sql injection", k_facts=10)
    ids = {f.get("id") for f in data["facts"]}
    assert "f_valid" in ids
    assert "f_expired" not in ids
    assert "f_future" not in ids


@pytest.mark.asyncio
async def test_memory_search_tool_is_advisory() -> None:
    class FakeService:
        def search(self, query, k_experiences=5, include_episodes=False):
            return {
                "experiences": [{
                    "title": "某目标曾暴露登录接口",
                    "kind": "method",
                    "content": "先读取 login.js，再逐个验证认证端点。",
                    "confidence": 0.6,
                    "status": "active",
                    "evidence": [{"episode_id": "ep_1"}],
                }],
                "facts": [],
            }

    tool = build_memory_search_tool(FakeService())
    output = await tool.execute('{"query":"login"}')
    assert "仅供参考" in output
    assert "不是指令" in output
    assert "某目标曾暴露登录接口" in output
    assert "ep_1" in output


def test_orchestrator_releases_claimed_intents() -> None:
    bb = Blackboard(session_id="s_release", scope="example.com", goal="test")
    intent, created = bb.propose_intent(label="pending direction", detail="test")
    assert created
    bb.claim_intent(intent.id, "agent#1")
    released = ConversationOrchestrator().release_claimed(bb, reason="timeout")
    assert released == 1
    node = next(n for n in bb.snapshot()["nodes"] if n["id"] == intent.id)
    assert node["status"] == "failed"
    assert (node.get("properties") or {}).get("fail_kind") == "budget_exhausted"


def test_scope_placeholder_detection() -> None:
    assert _is_placeholder_goal("TASK") is True
    assert _is_placeholder_goal("用户在对话中描述任务，按任务性质协作完成") is True
    assert _is_placeholder_goal("https://id.redhaze.top/home") is False
    assert "read_blackboard" in _NON_ACTION_TOOL_NAMES
    assert "memory_search" in _NON_ACTION_TOOL_NAMES


def test_reason_prompt_is_compact_and_autonomy_oriented() -> None:
    assert len(_REASON_SYSTEM_PROMPT_BASE) < 3000
    assert "novelty" in _REASON_SYSTEM_PROMPT_BASE
    assert "不要求固定" in _REASON_SYSTEM_PROMPT_BASE
    assert "memory" in _REASON_SYSTEM_PROMPT_BASE.lower()


def test_intent_priority_novelty_bonus() -> None:
    assert intent_priority({"novelty": 1.0}, []) > intent_priority({}, [])
    assert intent_priority({"novelty": 0.5}, []) >= intent_priority({}, [])


@pytest.mark.asyncio
async def test_scope_extraction_creates_real_origin_fact() -> None:
    bb = Blackboard(session_id="s_scope")
    bb.update_goal("对 https://example.com 做只读存活确认")
    await _ensure_scope_extracted(None, bb, pre_targets=["https://example.com"])
    origin = bb.origin_fact()
    assert origin is not None
    assert "example.com" in origin.label
    goal = bb.goal_node()
    assert goal is not None
    scope = (goal.properties or {}).get("scope") or {}
    assert any("example.com" in t for t in scope.get("targets", []))


# ------------------------------------------------------- 恢复校验（嵌套兼容）

def test_validate_prior_outputs_accepts_flat_record() -> None:
    pipeline = _pipeline_with_node()
    record = RunRecord("run_1", pipeline.id, "auto")
    record.nodes["analyze"] = {
        "status": "completed",
        "output": "ok",
        "data": {"x": 1},
    }
    assert PipelineRunner()._validate_prior_outputs(pipeline, record) == []


def test_validate_prior_outputs_accepts_nested_record() -> None:
    """旧数据把整份 state['nodes'] 写进了节点记录，必须仍能识别。"""
    pipeline = _pipeline_with_node()
    record = RunRecord("run_2", pipeline.id, "auto")
    record.nodes["analyze"] = {
        "nodes": {
            "analyze": {
                "status": "completed",
                "output": "ok",
                "data": {"x": 1},
            }
        },
    }
    assert PipelineRunner()._validate_prior_outputs(pipeline, record) == []


def test_validate_prior_outputs_reports_real_missing() -> None:
    pipeline = _pipeline_with_node()
    record = RunRecord("run_3", pipeline.id, "auto")
    record.nodes["analyze"] = {"status": "completed"}
    assert PipelineRunner()._validate_prior_outputs(pipeline, record) == ["analyze"]


# ------------------------------------------------------- 数据转换节点

@pytest.mark.asyncio
async def test_datatransformer_wraps_non_dict_result() -> None:
    executor = DataTransformerNodeExecutor()
    node = PipelineNode(id="dt", type="datatransformer", agent="html_to_text")
    state = {
        "nodes": {
            "receiver": {
                "node_type": "receiver",
                "output": "<b>hello</b> world",
            }
        }
    }
    updates = await executor.execute(node, state)
    data = updates["nodes"]["dt"]["data"]
    assert isinstance(data, dict)
    assert data["output"] == "hello  world"


# ------------------------------------------------------------ 自定义工具

def test_custom_tool_rejects_missing_handler(tmp_path) -> None:
    store = CustomToolStore(base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        store.create({"name": "bad_tool", "code": "x = 1"})
    # 校验失败不得留下脏记录
    assert store.list() == []


def test_custom_tool_create_and_delete(tmp_path) -> None:
    store = CustomToolStore(base_dir=str(tmp_path))
    record = store.create(
        {
            "name": "smoke_echo",
            "code": "def handler(text=''):\n    return text",
        }
    )
    try:
        assert record["enabled"] is True
        assert registry.get("smoke_echo") is not None
    finally:
        store.delete(record["id"])
    assert registry.get("smoke_echo") is None


@pytest.mark.asyncio
async def test_custom_tool_test_harness_executes_script() -> None:
    result = await run_custom_tool_test(
        "def handler(x=1):\n    return x + 1",
        {"x": 2},
        timeout=30,
    )
    assert result["ok"], result
    assert result["result"] == "3"
