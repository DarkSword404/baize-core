"""
流水线共享状态定义。

每个流水线执行期间维护一个 PipelineState 对象，在 LangGraph 节点间传递。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, Optional, TypedDict


class NodeRunRecord(TypedDict, total=False):
    """单个节点的执行记录。"""
    node_id: str
    node_type: str                # agent | decision | parallel | confirm | transform | subpipeline
    status: str                   # pending | running | completed | failed | paused
    input: Any                    # 节点输入（上下文快照）
    output: str                   # 节点输出文本
    data: dict[str, Any]          # 结构化数据（如 threat_score, json 解析结果）
    error: str                    # 错误信息
    started_at: float             # 开始时间戳
    ended_at: float               # 结束时间戳


PipelinePipeType = Literal["auto", "manual"]
PipelineRunStatus = Literal["pending", "running", "completed", "failed", "paused"]


class PipelineState(TypedDict, total=False):
    """Pipeline 全局状态 — LangGraph 节点间共享上下文。

    借鉴 LangGraph StateGraph 的 Annotated 合并机制：
    - messages 使用 operator.add 追加（不覆盖）
    - route 由每个节点显式设置下一步方向
    """

    # ---- 管道标识 ----
    pipeline_id: str
    run_id: str
    pipe_type: PipelinePipeType   # "auto" | "manual"

    # ---- 输入 ----
    context: dict[str, Any]       # 触发器传入的上下文
    webhook: str                  # 自动化完成后的回调 URL
    start_node_id: str            # 入口节点 ID

    # ---- 执行 ----
    current_node: str             # 当前正在执行的 node_id
    nodes: dict[str, NodeRunRecord]  # node_id → 执行记录

    # ---- 人工交互（仅 manual 管道）----
    confirm_required: bool        # 是否等待人工确认
    confirm_prompt: str           # 确认提示信息
    confirm_node_id: str          # 哪个节点触发了确认请求
    confirm_options: list[str]    # 确认选项列表
    human_response: str           # 人工响应: "approve" | "reject"

    # ---- 消息历史 ----
    messages: Annotated[list[dict[str, str]], operator.add]

    # ---- 输出 ----
    status: PipelineRunStatus     # pending | running | completed | failed | paused
    error: str
    report: str

    # ---- 图路由 ----
    route: str                    # 条件分支选择的目的节点 ID


def build_initial_state(
    pipeline_id: str,
    run_id: str,
    pipe_type: PipelinePipeType = "auto",
    context: dict[str, Any] | None = None,
    webhook: str = "",
    start_node_id: str = "",
) -> PipelineState:
    """为一次流水线执行构建初始状态。"""
    return PipelineState(
        pipeline_id=pipeline_id,
        run_id=run_id,
        pipe_type=pipe_type,
        context=context or {},
        webhook=webhook,
        start_node_id=start_node_id,
        current_node="",
        nodes={},
        confirm_required=False,
        confirm_prompt="",
        confirm_node_id="",
        confirm_options=[],
        human_response="",
        messages=[],
        status="pending",
        error="",
        report="",
        route="",
    )
