"""Agent hooks 瀑布式事件链测试（多处理器叠加 / 短路拦截 / 参数改写 / 会话日志）。"""

from __future__ import annotations

import pytest

from baize.sdk.agent import Agent
from baize.sdk.session_log import SessionLog


def _make_agent(**hooks) -> Agent:
    return Agent(name="test", hooks=hooks)


@pytest.mark.asyncio
async def test_no_handlers_passthrough() -> None:
    agent = _make_agent()
    allowed, final_args, reason = await agent._tool_call_chain("nmap_scan", '{"host": "x"}')
    assert allowed is True
    assert final_args == '{"host": "x"}'
    assert reason is None


@pytest.mark.asyncio
async def test_legacy_handler_auto_continue() -> None:
    """旧式签名 (agent, tool_name, arguments) 无 next 参数，应自动继续链。"""
    calls: list[str] = []

    async def legacy(agent, name, args):
        calls.append(f"legacy:{name}")

    agent = _make_agent(on_tool_call=legacy)
    allowed, final_args, reason = await agent._tool_call_chain("dns_lookup", "{}")
    assert allowed is True
    assert calls == ["legacy:dns_lookup"]


@pytest.mark.asyncio
async def test_waterfall_multiple_handlers_in_order() -> None:
    """多个瀑布式处理器应按注册顺序执行，且参数改写可传递。"""
    order: list[str] = []

    async def h1(agent, name, args, next):
        order.append("h1")
        return await next('{"a": 1}')  # 改写参数

    async def h2(agent, name, args, next):
        order.append(f"h2:{args}")
        return await next()

    agent = _make_agent(on_tool_call=[h1, h2])
    allowed, final_args, reason = await agent._tool_call_chain("tool_x", "{}")
    assert allowed is True
    assert order == ["h1", 'h2:{"a": 1}']
    assert final_args == '{"a": 1}'


@pytest.mark.asyncio
async def test_deny_short_circuits() -> None:
    """返回 {"deny": True} 应立即拦截，后续处理器不执行。"""
    order: list[str] = []

    async def blocker(agent, name, args, next):
        order.append("blocker")
        return {"deny": True, "reason": "目标在内网"}

    async def unreachable(agent, name, args, next):
        order.append("unreachable")
        return await next()

    agent = _make_agent(on_tool_call=[blocker, unreachable])
    allowed, final_args, reason = await agent._tool_call_chain("nmap_scan", "{}")
    assert allowed is False
    assert reason == "目标在内网"
    assert order == ["blocker"]  # 短路，后续不执行


@pytest.mark.asyncio
async def test_deny_reason_default() -> None:
    async def blocker(agent, name, args, next):
        return {"deny": True}

    agent = _make_agent(on_tool_call=blocker)
    allowed, final_args, reason = await agent._tool_call_chain("x", "{}")
    assert allowed is False
    assert reason == "未说明"


@pytest.mark.asyncio
async def test_mixed_legacy_and_waterfall() -> None:
    """旧式与瀑布式处理器混合时，旧式自动继续、瀑布式可拦截。"""
    order: list[str] = []

    async def legacy(agent, name, args):
        order.append("legacy")

    async def blocker(agent, name, args, next):
        order.append("blocker")
        return {"deny": True, "reason": "no"}

    agent = _make_agent(on_tool_call=[legacy, blocker])
    allowed, _, reason = await agent._tool_call_chain("x", "{}")
    assert allowed is False
    assert order == ["legacy", "blocker"]


@pytest.mark.asyncio
async def test_hook_handler_list_emit() -> None:
    """_emit 支持值列表（多个 on_tool_result 监听器全部触发）。"""
    fired: list[str] = []
    agent = _make_agent(
        on_tool_result=[
            lambda a, n, r: fired.append("r1"),
            lambda a, n, r: fired.append("r2"),
        ]
    )
    await agent._emit("on_tool_result", agent, "tool", "out")
    assert fired == ["r1", "r2"]


@pytest.mark.asyncio
async def test_session_log_records_events() -> None:
    """配置 SessionLog 后，_run_tool_loop 应记录完整事件流（含工具调用）。"""
    from baize.sdk.agent import AgentTool

    log = SessionLog()

    async def fake_tool(host: str) -> str:
        return f"scan {host} done"

    tool = AgentTool(name="port_scan", description="port scan", parameters={"type": "object", "properties": {"host": {"type": "string"}}, "required": ["host"]}, handler=fake_tool)

    agent = Agent(name="t", tools=[tool], session_log=log)
    agent._ensure_session_started()
    agent._log_event("user/message", content="hi")

    # 直接调用工具链，验证日志记录
    from baize.sdk.client import ChatMessage

    result = await tool.execute('{"host": "127.0.0.1"}')
    assert result == "scan 127.0.0.1 done"

    agent._log_event("tool/call", name="port_scan", arguments={"host": "127.0.0.1"})
    agent._log_event("tool/result", name="port_scan", output=result, denied=False)

    kinds = [e.kind for e in log]
    assert "session/start" in kinds
    assert "user/message" in kinds
    assert "tool/call" in kinds
    assert "tool/result" in kinds


@pytest.mark.asyncio
async def test_session_log_derive_messages_with_agent() -> None:
    """会话日志可通过 derive_messages 重建模型历史。"""
    from baize.sdk.agent import AgentTool
    from baize.sdk.client import ChatMessage

    log = SessionLog()
    agent = Agent(name="t", tools=[], session_log=log)
    agent._ensure_session_started()
    agent._log_event("user/message", content="hello")
    agent._log_event(
        "agent/response",
        content="calling",
        tool_calls=[{"id": "c1", "type": "function", "function": {"name": "dns_lookup", "arguments": "{}"}}],
    )
    agent._log_event("tool/result", name="dns_lookup", output="1.2.3.4", call_id="c1")

    msgs = log.derive_messages()
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "c1"
