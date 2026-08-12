"""
节点类型定义 — 流水线的原子构建块。

6 种节点类型对应 6 种执行语义：

    agent        调用 LLM Agent 执行安全任务
    decision     条件分支，根据上游输出路由到不同下游节点
    parallel     并行执行多个子节点
    confirm      人工确认点（仅 manual 管道可用）
    transform    数据转换/清洗（不调 LLM）
    subpipeline  嵌套子流水线
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


NodeTypeKind = Literal["agent", "decision", "parallel", "confirm", "transform", "subpipeline", "receiver", "datatransformer"]


@dataclass
class BranchRule:
    """决策节点的单条分支规则。"""
    condition: str               # Jinja2 模板表达式，如 "steps.triage.data.threat_score > 0.8"
    target: str                  # 条件为真时跳转的目标 node_id
    label: str = ""              # 人类可读标签
    is_default: bool = False


@dataclass
class ParallelBranch:
    """并行节点的单个分支。"""
    node_id: str                 # 分支对应的子节点 ID（可直接是子 pipeline 定义）
    node: "PipelineNode | None" = None  # 内联子节点定义


# ---- 节点类型定义 ----

@dataclass
class PipelineNode:
    """流水线节点通用结构。"""
    id: str
    type: NodeTypeKind
    display_name: str = ""
    description: str = ""

    # ---- agent / transform 通用 ----
    agent: str = ""              # agent 节点：Agent ID；transform 节点：内置转换器名
    prompt_template: str = ""    # Jinja2 提示词模板

    # ---- decision 专用 ----
    branches: list[BranchRule] = field(default_factory=list)
    decision_expression: str = ""  # 简化的决策表达式

    # ---- parallel 专用 ----
    parallel_branches: list[ParallelBranch] = field(default_factory=list)
    merge_strategy: Literal["all", "first", "none"] = "all"

    # ---- confirm 专用 ----
    confirm_prompt: str = ""
    confirm_options: list[str] = field(default_factory=list)
    confirm_branches: dict[str, str] = field(default_factory=dict)

    # ---- node (重定向目标，避免与 built-in 冲突) ----
    target: str = ""             # 显式指定的下一个节点（覆盖路由推断）

    # ---- subpipeline 专用 ----
    sub_nodes: list["PipelineNode"] = field(default_factory=list)

    # ---- 超时 / 重试 ----
    timeout_seconds: int = 300
    max_retries: int = 1

    @property
    def is_human_node(self) -> bool:
        return self.type == "confirm"


@dataclass
class PipelineDefinition:
    """一条完整的流水线定义 — YAML 解析后的中间表示。"""
    id: str
    name: str
    type: Literal["auto", "manual"] = "auto"  # 管道类型硬约束
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)

    nodes: list[PipelineNode] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    context_schema: dict[str, Any] = field(default_factory=dict)

    version: str = "1.0"
    timeout_seconds: int = 3600
    max_concurrency: int = 1

    def get_start_node(self) -> PipelineNode | None:
        """获取入口节点。优先 receiver > transform/agent > 其他。"""
        for n in self.nodes:
            if n.type in ("receiver", "datatransformer", "transform", "agent", "parallel"):
                return n
        return self.nodes[0] if self.nodes else None

    def get_node(self, node_id: str) -> PipelineNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def validate(self) -> list[str]:
        """编译前校验，返回错误列表。"""
        from baize.orchestration.validators import validate_pipeline
        return validate_pipeline(self)
