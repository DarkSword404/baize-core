"""
编译时校验器 — 确保流水线定义的合法性。
"""

from __future__ import annotations

from baize.orchestration.node_types import PipelineDefinition


def validate_pipeline(pipeline: PipelineDefinition) -> list[str]:
    """返回错误列表，空列表表示合法。"""
    errors: list[str] = []

    # ---- 1. 节点 ID 唯一性 ----
    ids: set[str] = set()
    for node in pipeline.nodes:
        if node.id in ids:
            errors.append(f"节点 ID 重复: {node.id}")
        ids.add(node.id)

    # ---- 2. 管道类型硬约束 ----
    has_confirm = any(n.type == "confirm" for n in pipeline.nodes)
    if pipeline.type == "auto" and has_confirm:
        errors.append(
            "自动化管道 (type=auto) 不允许包含 confirm 节点。"
            "如需人工介入，请将 type 改为 manual。"
        )

    # ---- 3. manual 建议优化 ----
    if pipeline.type == "manual" and not has_confirm:
        errors.append(
            "[警告] 人工介入管道 (type=manual) 未包含 confirm 节点，"
            "建议降级为 type=auto。"
        )

    # ---- 4. 入口节点检查 ----
    start = pipeline.get_start_node()
    if start is None:
        errors.append("流水线至少需要一个非条件节点作为入口。")

    # ---- 5. decision 节点分支完整性 ----
    for node in pipeline.nodes:
        if node.type == "decision":
            has_default = any(b.is_default for b in node.branches)
            if not has_default:
                errors.append(
                    f"decision 节点 '{node.id}' 缺少默认分支 (is_default=True)。"
                    "请设置兜底路由，防止死循环或无出口。"
                )
            # 检查分支目标存在性
            for br in node.branches:
                if br.target not in ids:
                    errors.append(
                        f"decision 节点 '{node.id}' 的分支目标 '{br.target}' 不存在于节点列表中。"
                    )

    # ---- 6. confirm 节点分支检查 ----
    for node in pipeline.nodes:
        if node.type == "confirm":
            if not node.confirm_branches:
                errors.append(f"confirm 节点 '{node.id}' 未定义 confirm_branches (如 approve→, reject→)。")

    # ---- 7. parallel 节点子节点检查 ----
    for node in pipeline.nodes:
        if node.type == "parallel":
            if not node.parallel_branches:
                errors.append(f"parallel 节点 '{node.id}' 至少需要一个并行分支。")

    # ---- 8. agent 节点必要字段 ----
    for node in pipeline.nodes:
        if node.type == "agent" and not node.agent:
            errors.append(f"agent 节点 '{node.id}' 缺少 agent 字段 (Agent ID)。")
        if node.type == "agent" and not node.prompt_template:
            errors.append(
                f"agent 节点 '{node.id}' 缺少 prompt_template 字段。"
                "自动化管道需要明确的提示词模板。"
            )

    return errors
