"""
白泽编排模块 (Baize Orchestration)

提供流水线编排能力：
- 6 种节点类型：agent, decision, parallel, confirm, transform, subpipeline
- 后台执行引擎：提交后立即返回，执行在后台异步进行
- SSE 事件流：实时进度 + 断线重连 + 事件补齐
- 人工确认：confirm 节点暂停等待，Command 恢复执行
- 预置模板：SOC 研判 / 渗透测试 / 漏洞扫描 / 钓鱼分析
"""

from baize.orchestration.state import PipelineState, build_initial_state, PipelinePipeType, PipelineRunStatus
from baize.orchestration.node_types import PipelineDefinition, PipelineNode, BranchRule, ParallelBranch, NodeTypeKind
from baize.orchestration.compiler import PipelineGraphCompiler, compile_pipeline, execute_pipeline
from baize.orchestration.run_store import RunRecord, RunStore, get_run_store
from baize.orchestration.runner import PipelineRunner, get_runner
from baize.orchestration.templates import get_builtin_templates, get_template_by_id
from baize.orchestration.validators import validate_pipeline

# 向后兼容
from baize.orchestration.yaml_loader import load_pipeline_from_yaml

# FastAPI 路由注册入口
from baize.orchestration.api import register  # noqa: E402


# 保持旧接口兼容（逐步废弃）
def get_graph(pipeline_id: str):
    """[已废弃] 请使用 PipelineGraphCompiler。"""
    tpl = get_template_by_id(pipeline_id)
    if tpl is None:
        return None
    from baize.orchestration.api import _build_pipeline_from_yaml
    pipeline = _build_pipeline_from_yaml(tpl)
    return PipelineGraphCompiler(pipeline).compile()


__all__ = [
    # State
    "PipelineState",
    "PipelinePipeType",
    "PipelineRunStatus",
    "build_initial_state",
    # Node Types
    "PipelineDefinition",
    "PipelineNode",
    "BranchRule",
    "ParallelBranch",
    "NodeTypeKind",
    # Compiler
    "PipelineGraphCompiler",
    "compile_pipeline",
    "execute_pipeline",
    # Runner
    "RunRecord",
    "RunStore",
    "get_run_store",
    "PipelineRunner",
    "get_runner",
    # Templates
    "get_builtin_templates",
    "get_template_by_id",
    # Validators
    "validate_pipeline",
    # Legacy
    "load_pipeline_from_yaml",
    "get_graph",
    # API
    "register",
]
