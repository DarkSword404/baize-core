"""
流水线编译器 — 将 PipelineDefinition 编译为 LangGraph StateGraph。

核心设计（借鉴 LangGraph / Dify 模式）：
1. 每种节点类型注册为一个图节点函数
2. decision 节点用 add_conditional_edges 实现非顺序路由
3. confirm 节点用 LangGraph interrupt() 实现人工确认暂停
4. parallel 节点内部用 asyncio.gather 实现并发
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.base import BaseCheckpointSaver

from baize.orchestration.state import PipelineState, build_initial_state
from baize.orchestration.node_types import PipelineDefinition, PipelineNode
from baize.orchestration.nodes.base import BaseNodeExecutor
from baize.orchestration.nodes.parallel import get_executor

logger = logging.getLogger(__name__)

# === LangGraph 节点函数名前缀，避免冲突 ===
NODE_PREFIX = "_pnode_"


class PipelineGraphCompiler:
    """将 PipelineDefinition 编译为可执行的 LangGraph StateGraph。"""

    def __init__(
        self,
        pipeline: PipelineDefinition,
        checkpointer: BaseCheckpointSaver | None = None,
    ):
        self.pipeline = pipeline
        self.checkpointer = checkpointer or MemorySaver()
        self._node_names: dict[str, str] = {}  # node_id → LangGraph node name
        self._decision_nodes: set[str] = set()
        self._confirm_nodes: set[str] = set()
        self._parallel_nodes: set[str] = set()

        # 执行器缓存
        self._executors: dict[str, BaseNodeExecutor] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile(self) -> StateGraph:
        """编译管道为 StateGraph，返回已编译的图对象。"""
        graph = StateGraph(PipelineState)

        self._register_nodes(graph)
        self._wire_edges(graph)

        return graph.compile(checkpointer=self.checkpointer)

    async def execute(
        self,
        context: dict[str, Any],
        webhook: str = "",
        config: dict[str, Any] | None = None,
    ) -> PipelineState:
        """直接执行（同步等待完成）。"""
        compiled = self.compile()
        run_id = str(uuid.uuid4())
        start = self.pipeline.get_start_node()
        initial = build_initial_state(
            pipeline_id=self.pipeline.id,
            run_id=run_id,
            pipe_type=self.pipeline.type,
            context=context,
            webhook=webhook,
            start_node_id=start.id if start else "",
        )
        cfg = config or {"configurable": {"thread_id": run_id}}
        result = await compiled.ainvoke(initial, cfg)
        return result

    async def execute_stream(
        self,
        context: dict[str, Any],
        webhook: str = "",
        config: dict[str, Any] | None = None,
        on_event: Callable[[str, dict[str, Any]], Any] | None = None,
    ):
        """流式执行（通过回调推送每个节点的事件）。"""
        compiled = self.compile()
        run_id = str(uuid.uuid4())
        start = self.pipeline.get_start_node()
        initial = build_initial_state(
            pipeline_id=self.pipeline.id,
            run_id=run_id,
            pipe_type=self.pipeline.type,
            context=context,
            webhook=webhook,
            start_node_id=start.id if start else "",
        )
        cfg = config or {"configurable": {"thread_id": run_id}}

        async for event in compiled.astream_events(initial, cfg, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")
            metadata = event.get("metadata", {})

            if kind == "on_chain_start" and name.startswith(NODE_PREFIX):
                node_id = name[len(NODE_PREFIX):]
                data = event.get("data", {})
                inp = data.get("input", {}) if isinstance(data, dict) else {}
                if on_event:
                    on_event("node_started", {
                        "run_id": run_id,
                        "node_id": node_id,
                        "node_type": inp.get("current_node_type", ""),
                    })

            elif kind == "on_chain_end" and name.startswith(NODE_PREFIX):
                node_id = name[len(NODE_PREFIX):]
                data = event.get("data", {})
                output = data.get("output", {}) if isinstance(data, dict) else {}
                if on_event:
                    event_type = "node_completed"
                    if output.get("confirm_required"):
                        event_type = "pipeline_paused"
                    on_event(event_type, {
                        "run_id": run_id,
                        "node_id": node_id,
                        "data": output,
                    })

        # 读取最终状态
        final = compiled.get_state(cfg)
        if final and final.values:
            if on_event:
                status = final.values.get("status", "completed")
                if status == "completed":
                    on_event("pipeline_completed", {"run_id": run_id, "data": final.values})
                elif status == "failed":
                    on_event("pipeline_failed", {"run_id": run_id, "data": final.values})

        yield run_id, final.values if final else initial

    def get_confirm_node_state(self, run_id: str) -> dict[str, Any] | None:
        """获取等待人工确认的节点状态。"""
        compiled = self.compile()
        state = compiled.get_state({"configurable": {"thread_id": run_id}})
        if state and state.values:
            return state.values
        return None

    async def resume_after_confirm(self, run_id: str, choice: str) -> PipelineState:
        """人工确认后恢复执行。"""
        from langgraph.types import Command
        compiled = self.compile()
        cfg = {"configurable": {"thread_id": run_id}}
        result = await compiled.ainvoke(Command(resume=choice), cfg)
        return result

    # ------------------------------------------------------------------
    # 节点注册
    # ------------------------------------------------------------------

    def _register_nodes(self, graph: StateGraph) -> None:
        """将所有管道节点注册为 LangGraph 图节点。"""
        for node in self.pipeline.nodes:
            if node.type == "decision":
                self._decision_nodes.add(node.id)
            elif node.type == "confirm":
                self._confirm_nodes.add(node.id)
            elif node.type == "parallel":
                self._parallel_nodes.add(node.id)

            # 每个节点只注册一个执行函数
            langgraph_name = f"{NODE_PREFIX}{node.id}"
            self._node_names[node.id] = langgraph_name

            executor = get_executor(node.type)
            self._executors[node.id] = executor

            graph.add_node(langgraph_name, self._make_node_func(node))

    # ------------------------------------------------------------------
    # 边连接
    # ------------------------------------------------------------------

    def _wire_edges(self, graph: StateGraph) -> None:
        """连接节点之间的边，处理条件和人工确认路由。"""
        nodes = self.pipeline.nodes

        if not nodes:
            graph.set_entry_point(f"{NODE_PREFIX}__empty__")

            async def _empty(state: PipelineState) -> dict:
                return {"status": "completed", "report": "空流水线"}

            graph.add_node(f"{NODE_PREFIX}__empty__", _empty)
            graph.add_edge(f"{NODE_PREFIX}__empty__", END)
            return

        # 入口：第一个节点
        start = self.pipeline.get_start_node()
        if start:
            graph.set_entry_point(self._node_names[start.id])

        for i, node in enumerate(nodes):
            lang_name = self._node_names[node.id]

            if node.type == "decision":
                # 条件边：根据 branches 构建路由函数
                graph.add_conditional_edges(
                    lang_name,
                    self._make_decision_router(node),
                    self._build_decision_path_map(node),
                )

            elif node.type == "confirm":
                # confirm 节点：完成后根据 confirm_branches 路由
                graph.add_conditional_edges(
                    lang_name,
                    self._make_confirm_router(node),
                    self._build_confirm_path_map(node),
                )

            elif node.type in ("agent", "transform", "subpipeline", "parallel"):
                # 普通节点：顺序连接到下一个节点
                next_node = self._find_next_node(node.id)
                if next_node:
                    graph.add_edge(lang_name, self._node_names[next_node.id])
                else:
                    graph.add_edge(lang_name, END)

    def _find_next_node(self, current_id: str) -> PipelineNode | None:
        """找到当前节点的下一个（线性）节点。"""
        found_current = False
        for node in self.pipeline.nodes:
            if found_current and node.type not in ("decision",):
                # decision 节点不作为顺序边的目标
                # 它们是纯条件路由，必须在 conditional_edges 中处理
                pass
            if found_current and node.type != "decision":
                return node
            if node.id == current_id:
                found_current = True
        return None

    # ------------------------------------------------------------------
    # 路由函数工厂
    # ------------------------------------------------------------------

    def _make_node_func(self, node: PipelineNode) -> Callable:
        """为节点生成 LangGraph 节点执行函数。"""

        async def _execute_node(state: PipelineState) -> dict[str, Any]:
            executor = self._executors[node.id]
            result = await executor.execute(node, state)
            if isinstance(result, dict):
                result["current_node_type"] = node.type
            return result

        return _execute_node

    def _make_decision_router(self, node: PipelineNode) -> Callable:
        """生成 decision 节点的条件路由函数。"""

        def _route(state: PipelineState) -> str:
            route = state.get("route", "")
            if route:
                return route
            # 回退到默认分支
            for br in node.branches:
                if br.is_default:
                    return br.target
            return node.branches[0].target if node.branches else "__end__"

        return _route

    def _build_decision_path_map(self, node: PipelineNode) -> dict[str, str]:
        """构建 decision 节点的 path_map。"""
        path_map: dict[str, str] = {}
        for br in node.branches:
            target_name = self._node_names.get(br.target, END)
            path_map[br.target] = target_name
        path_map.setdefault("__end__", END)
        return path_map

    def _make_confirm_router(self, node: PipelineNode) -> Callable:
        """生成 confirm 节点的路由函数。"""

        def _route(state: PipelineState) -> str:
            choice = state.get("human_response", "")
            route = node.confirm_branches.get(choice, "")
            if route:
                return route
            # 默认回退
            return list(node.confirm_branches.values())[0] if node.confirm_branches else "__end__"

        return _route

    def _build_confirm_path_map(self, node: PipelineNode) -> dict[str, str]:
        """构建 confirm 节点的 path_map。"""
        path_map: dict[str, str] = {}
        for choice, target in node.confirm_branches.items():
            path_map[target] = self._node_names.get(target, END)
        path_map.setdefault("__end__", END)
        return path_map


# ====================================================================
# 便捷函数
# ====================================================================

def compile_pipeline(
    pipeline_def: PipelineDefinition,
    checkpointer: BaseCheckpointSaver | None = None,
) -> StateGraph:
    """快捷方法：将管道定义编译为 LangGraph 图。"""
    return PipelineGraphCompiler(pipeline_def, checkpointer).compile()


async def execute_pipeline(
    pipeline_def: PipelineDefinition,
    context: dict[str, Any],
    webhook: str = "",
    config: dict[str, Any] | None = None,
) -> PipelineState:
    """快捷方法：编译并执行一条流水线。"""
    compiler = PipelineGraphCompiler(pipeline_def)
    return await compiler.execute(context, webhook, config)
