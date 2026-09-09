"""动态 agent 闭环测试：模拟告警研判场景，验证 reason→dynamic_agent→reason 循环。

走真实代码路径 + 配置的 LLM，验证：
1. reason 节点读黑板 → LLM 生成 AgentSpec（role_prompt + capability_tags）
2. dynamic_agent 节点读 state.agent_spec → 工厂实例化临时 agent → 执行
3. agent 写回黑板 Fact + 新 Intent → 回 reason 循环
"""
import asyncio
import sys
sys.path.insert(0, "/workspace/src")
sys.path.insert(0, "/workspace/baize-orchestration/src")

from baize.pentest.blackboard import Blackboard
from baize.pentest.dynamic_agent import AgentSpec, get_factory
from baize.orchestration.nodes.reason import ReasonNodeExecutor
from baize.orchestration.nodes.agent import AgentNodeExecutor
from baize.orchestration.node_types import PipelineNode, BranchRule
from baize.orchestration.state import PipelineState

# 模拟告警研判任务
TASK = "研判以下告警：检测到 192.168.1.105 在 10 分钟内对 10.0.0.5 的 445 端口发起 2000+ 次 SMB 登录失败尝试"
GOAL = "完成告警研判，给出定性结论与处置建议"

async def main():
    # 1. 初始化黑板（模拟会话黑板）
    bb = Blackboard(session_id="test-dyn", scope=TASK, goal=GOAL)
    print(f"[黑板] 初始化完成: facts={len(bb.facts())}, intents={len(bb.pending_intents())}")
    for i in bb.pending_intents():
        print(f"  pending intent: {i.id} - {i.label}")

    # 2. 构造 reason 节点（单循环骨架）
    reason_node = PipelineNode(
        id="reason",
        type="reason",
        display_name="黑板调度",
        branches=[
            BranchRule(target="dynamic_agent", label="执行动态agent", condition="生成临时agent"),
            BranchRule(target="end", label="收尾", condition="任务完成", is_default=True),
        ],
    )

    # 3. 构造 dynamic_agent 节点
    agent_node = PipelineNode(
        id="dynamic_agent",
        type="agent",
        agent="",
        target="reason",
        prompt_template="任务: {{ context.task }}\n目标: {{ context.goal }}\n执行并输出 findings。",
    )

    # 4. 构造 state（注入 task/goal，模拟 stream_message 的 context 注入）
    state: PipelineState = {
        "context": {
            "session_id": "test-dyn",
            "task": TASK,
            "goal": GOAL,
            "model": None,
        },
        "nodes": {},
        "dialog": [],
        "messages": [],
    }

    # 5. mock _get_blackboard 让节点能反查到我们的黑板
    reason_exec = ReasonNodeExecutor()
    agent_exec = AgentNodeExecutor()
    reason_exec._get_blackboard = lambda s: bb
    agent_exec._get_blackboard = lambda s: bb

    # 也 mock _get_app_state（agent 节点动态实例化时取 session_log 用）
    import baize.api.app as appmod
    class _FakeSess:
        blackboard = bb
        session_log = None
    class _FakeAppState:
        session_manager = type("M", (), {"get_session": lambda self, sid: _FakeSess()})()
    appmod._get_app_state = lambda: _FakeAppState()

    print("\n=== 第 1 轮 reason ===")
    updates1 = await reason_exec.execute(reason_node, state)
    print(f"route: {updates1.get('route')}")
    print(f"agent_spec: {updates1.get('agent_spec', {}).get('role_prompt', '')[:80]}")
    print(f"capability_tags: {updates1.get('agent_spec', {}).get('capability_tags')}")
    print(f"fallback: {updates1.get('data', {}).get('fallback')}")
    print(f"error: {updates1.get('data', {}).get('error')}")

    # 合并 updates 进 state（模拟 runner 行为）
    state.update(updates1)
    # 清掉 nodes 里 reason 的临时记录（避免累积）
    state["nodes"] = {}

    if updates1.get("agent_spec", {}).get("role_prompt"):
        print("\n=== 第 1 轮 dynamic_agent ===")
        updates2 = await agent_exec.execute(agent_node, state)
        print(f"agent_role_tag: {updates2.get('dialog', [{}])[0].get('agent', '')}")
        print(f"output preview: {(updates2.get('dialog', [{}])[0].get('output', '') or '')[:120]}")
        state.update(updates2)
        state["nodes"] = {}

        print(f"\n[黑板] 第1轮后: facts={len(bb.facts())}, intents={len(bb.pending_intents())}")
        for f in bb.facts()[-3:]:
            print(f"  fact: {f.get('label', f)}")
        for i in bb.pending_intents()[:3]:
            print(f"  pending intent: {i.id} - {i.label}")

        print("\n=== 第 2 轮 reason ===")
        updates3 = await reason_exec.execute(reason_node, state)
        print(f"route: {updates3.get('route')}")
        print(f"agent_spec: {updates3.get('agent_spec', {}).get('role_prompt', '')[:80]}")
        print(f"capability_tags: {updates3.get('agent_spec', {}).get('capability_tags')}")
        print(f"fallback: {updates3.get('data', {}).get('fallback')}")
        state.update(updates3)
        state["nodes"] = {}

        if updates3.get("agent_spec", {}).get("role_prompt"):
            print("\n=== 第 2 轮 dynamic_agent ===")
            updates4 = await agent_exec.execute(agent_node, state)
            print(f"output preview: {(updates4.get('dialog', [{}])[0].get('output', '') or '')[:120]}")
            state.update(updates4)
            state["nodes"] = {}

            print(f"\n[黑板] 第2轮后: facts={len(bb.facts())}, intents={len(bb.pending_intents())}")

            print("\n=== 第 3 轮 reason（应判断是否收尾）===")
            updates5 = await reason_exec.execute(reason_node, state)
            print(f"route: {updates5.get('route')}")
            print(f"fallback: {updates5.get('data', {}).get('fallback')}")
            print(f"reasoning: {updates5.get('data', {}).get('reasoning', '')}")

    print(f"\n[黑板] 最终: facts={len(bb.facts())}, version={bb.version()}")
    print("闭环测试完成")

asyncio.run(main())
