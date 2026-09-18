"""
节点类型定义 — 流水线的原子构建块。

    agent          调用 LLM Agent 执行安全任务
    decision       规则条件分支，根据上游输出路由到不同下游节点（兜底）
    ai_decision    AI 决策分支，LLM 综合上下文选择分支（AI-SOAR 核心）
    parallel       并行执行多个子节点
    confirm        人工确认点（仅 manual 管道可用）
    transform      数据转换/清洗（不调 LLM）
    subpipeline    嵌套子流水线
    receiver       外部数据接收节点（入口）
    datatransformer 数据转换节点（入口预处理）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


NodeTypeKind = Literal[
    "agent", "decision", "ai_decision", "parallel", "confirm", "transform",
    "subpipeline", "receiver", "datatransformer", "end",
]


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


@dataclass
class PipelineEdge:
    """图编排连线：source → target 的有向边。

    - 条件节点（decision/ai_decision/confirm）的路由仍由 branches /
      confirm_branches 描述，画布连线仅作为辅助展示；
    - 普通节点（agent/transform/receiver/end/subpipeline/parallel）的
      默认流转以 edges 为准（若提供），未提供时回退到节点列表顺序推断，
      保证旧模板兼容。
    """
    source: str
    target: str
    label: str = ""              # 边标签（可读说明 / 条件分支名）


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

    # ---- decision / ai_decision 专用 ----
    branches: list[BranchRule] = field(default_factory=list)
    decision_expression: str = ""  # 简化的决策表达式
    decision_prompt: str = ""      # ai_decision：LLM 决策提示词（Jinja2）
    decision_model: str = ""       # ai_decision：可选，覆盖全局模型配置

    # ---- parallel 专用 ----
    parallel_branches: list[ParallelBranch] = field(default_factory=list)
    merge_strategy: Literal["all", "first", "none"] = "all"

    # ---- confirm 专用 ----
    confirm_prompt: str = ""
    confirm_options: list[str] = field(default_factory=list)
    confirm_branches: dict[str, str] = field(default_factory=dict)

    # ---- node (重定向目标，避免与 built-in 冲突) ----
    target: str = ""             # 显式指定的下一个节点（覆盖路由推断）

    # ---- end (结束对话节点) 专用 ----
    save_dialog: bool = False    # True=本次对话保留归档；False=默认回收删除

    # ---- subpipeline 专用 ----
    sub_nodes: list["PipelineNode"] = field(default_factory=list)

    # ---- 超时 / 重试 ----
    timeout_seconds: int = 300
    max_retries: int = 1

    # ---- 失败语义（SOAR 失败分支） ----
    error_target: str = ""       # 节点执行失败时的路由目标 node_id（可选）
    ignore_error: bool = False   # True=失败仅记录，继续走正常路径（不触发失败分支）

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
    edges: list[PipelineEdge] = field(default_factory=list)
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
