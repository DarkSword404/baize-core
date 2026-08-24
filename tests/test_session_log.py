"""Append-only 会话日志测试（单一事实源 + 审计重放 + 持久化）。"""

from __future__ import annotations

import os

import pytest

from baize.sdk.session_log import SessionEvent, SessionLog


def test_append_and_iter() -> None:
    log = SessionLog(session_id="s1")
    log.append("user/message", content="hello")
    log.append("tool/call", name="port_scan", arguments={"host": "127.0.0.1"})
    assert len(log) == 2
    events = list(log)
    assert events[0].kind == "user/message"
    assert events[0].seq == 0
    assert events[1].kind == "tool/call"
    assert events[1].payload["name"] == "port_scan"
    assert log.filter("tool/call") == [events[1]]


def test_unknown_kind_rejected() -> None:
    log = SessionLog()
    with pytest.raises(ValueError, match="未知事件类型"):
        log.append("no/such_kind")


def test_derive_messages() -> None:
    log = SessionLog()
    log.append("session/start")
    log.append("user/message", content="扫描目标")
    log.append(
        "agent/response",
        content="我来调用工具",
        tool_calls=[
            {"id": "call_1", "type": "function", "function": {"name": "port_scan", "arguments": "{}"}}
        ],
    )
    log.append("tool/result", name="port_scan", output="开放端口: 22", call_id="call_1")
    log.append("agent/response", content="扫描完成")

    msgs = log.derive_messages()
    assert msgs[0] == {"role": "user", "content": "扫描目标"}
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["tool_calls"] == [
        {"id": "call_1", "type": "function", "function": {"name": "port_scan", "arguments": "{}"}}
    ]
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "call_1"
    assert msgs[2]["content"] == "开放端口: 22"
    assert msgs[3] == {"role": "assistant", "content": "扫描完成"}


def test_replay_human_readable() -> None:
    log = SessionLog()
    log.append("user/message", content="hello")
    log.append("tool/call", name="dns_lookup", arguments={"domain": "example.com"})
    lines = log.replay()
    assert any("用户: hello" in l for l in lines)
    assert any("dns_lookup" in l for l in lines)


def test_file_persistence(tmp_path) -> None:
    path = os.path.join(tmp_path, "session.jsonl")
    log = SessionLog(session_id="s2", path=path)
    log.append("user/message", content="a")
    log.append("tool/call", name="x", arguments={})
    assert os.path.exists(path)

    log2 = SessionLog(session_id="s2", path=path)
    assert len(log2) == 2
    log2.append("agent/response", content="b")
    assert len(log2) == 3
    log3 = SessionLog(session_id="s2", path=path)
    assert len(log3) == 3  # 第三次加载也能看到新事件


def test_save_and_load(tmp_path) -> None:
    path = os.path.join(tmp_path, "export.jsonl")
    log = SessionLog()
    log.append("user/message", content="hello")
    log.save(path)
    loaded = SessionLog.load(path)
    assert len(loaded) == 1
    assert loaded.events[0].payload["content"] == "hello"


def test_session_event_roundtrip() -> None:
    ev = SessionEvent(seq=1, kind="system", payload={"message": "x"})
    d = ev.to_dict()
    ev2 = SessionEvent.from_dict(d)
    assert ev2.seq == ev.seq
    assert ev2.kind == ev.kind
    assert ev2.payload == ev.payload
    assert ev2.id == ev.id


def test_report_output() -> None:
    log = SessionLog(session_id="audit-1")
    log.append("session/start")
    log.append("user/message", content="test")
    log.append("session/end", reason="done")
    report = log.to_report()
    assert "audit-1" in report
    assert "会话结束" in report
