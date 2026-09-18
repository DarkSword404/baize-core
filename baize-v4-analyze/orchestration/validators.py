"""
编译时校验器 — 确保流水线定义的合法性。
"""

from __future__ import annotations

from baize.orchestration.node_types import PipelineDefinition


def _compute_reachable(pipeline: PipelineDefinition) -> set[str]:
    """从入口节点出发，沿边 / 分支目标 / parallel 子分支计算可达节点集合。"""
    start = pipeline.get_start_node()
    if start is None:
        return set()
    node_map = {n.id for n in pipeline.nodes}
    out: dict[str, list[str]] = {n.id: [] for n in pipeline.nodes}
    for e in pipeline.edges:
        if e.source in node_map and e.target in node_map:
            out[e.source].append(e.target)
    for n in pipeline.nodes:
        if n.type in ("decision", "ai_decision"):
            for b in n.branches:
                if b.target:
                    out[n.id].append(b.target)
        elif n.type == "confirm":
            for t in n.confirm_branches.values():
                if t:
                    out[n.id].append(t)
        elif n.type == "parallel":
            for pb in n.parallel_branches:
                if pb.node_id:
                    out[n.id].append(pb.node_id)
        if n.target:
            out[n.id].append(n.target)
    seen: set[str] = set()
    stack = [start.id]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for nxt in out.get(cur, []):
            if nxt not in seen:
                stack.append(nxt)
    return seen


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

    # ---- 5. decision / ai_decision 节点分支完整性 ----
    for node in pipeline.nodes:
        if node.type in ("decision", "ai_decision"):
            has_default = any(b.is_default for b in node.branches)
            if not has_default:
                errors.append(
                    f"{node.type} 节点 '{node.id}' 缺少默认分支 (is_default=True)。"
                    "请设置兜底路由，防止死循环或无出口。"
                )
            # 检查分支目标存在性
            for br in node.branches:
                if br.target not in ids:
                    errors.append(
                        f"{node.type} 节点 '{node.id}' 的分支目标 '{br.target}' 不存在于节点列表中。"
                    )

    # ---- 5.1 ai_decision 必要字段 ----
    for node in pipeline.nodes:
        if node.type == "ai_decision":
            if not node.decision_prompt:
                errors.append(
                    f"ai_decision 节点 '{node.id}' 缺少 decision_prompt 字段。"
                    "AI 决策需要明确的任务描述与判断标准。"
                )
            if not node.branches:
                errors.append(
                    f"ai_decision 节点 '{node.id}' 至少需要一个分支 (branches)。"
                    "请定义候选路由及默认分支。"
                )

    # ---- 6. confirm 节点分支检查 ----
    for node in pipeline.nodes:
        if node.type == "confirm":
            if not node.confirm_branches:
                errors.append(f"confirm 节点 '{node.id}' 未定义 confirm_branches (如 approve→, reject→)。")
            for opt, tgt in node.confirm_branches.items():
                if tgt and tgt not in ids:
                    errors.append(
                        f"confirm 节点 '{node.id}' 选项 '{opt}' 的路由目标 '{tgt}' 不存在于节点列表中。"
                    )

    # ---- 6.1 失败语义字段检查 ----
    for node in pipeline.nodes:
        if node.error_target and node.error_target not in ids:
            errors.append(
                f"节点 '{node.id}' 的 error_target '{node.error_target}' 不存在于节点列表中。"
            )
        if node.ignore_error and node.error_target:
            errors.append(
                f"节点 '{node.id}' 同时设置了 ignore_error 与 error_target，二者互斥，请只保留一项。"
            )

    # ---- 6.2 边（edges）结构检查 ----
    if pipeline.edges:
        # 失败分支边（source → source.error_target）不计入"默认出边"
        err_edges = {
            (n.id, n.error_target)
            for n in pipeline.nodes if n.error_target and not n.ignore_error
        }
        seen: set[tuple[str, str]] = set()
        out_deg: dict[str, int] = {}
        for e in pipeline.edges:
            if e.source not in ids:
                errors.append(f"边起点不存在: '{e.source}' 不是有效节点 ID。")
            if e.target not in ids:
                errors.append(f"边终点不存在: '{e.target}' 不是有效节点 ID。")
            if e.source == e.target:
                errors.append(f"自环边不允许: '{e.source}' → '{e.source}'，条件循环请使用 decision/confirm。")
            key = (e.source, e.target)
            if key in seen:
                errors.append(f"重复连线: '{e.source}' → '{e.target}'。")
            seen.add(key)
            if key not in err_edges:
                out_deg[e.source] = out_deg.get(e.source, 0) + 1

        # 普通节点扇出提示：运行时普通节点仅支持单条默认出边
        for n in pipeline.nodes:
            if n.type in ("decision", "ai_decision", "confirm", "parallel"):
                continue
            if out_deg.get(n.id, 0) > 1:
                errors.append(
                    f"[警告] 节点 '{n.id}' 有 {out_deg[n.id]} 条出边；普通节点默认只沿 1 条出边流转，"
                    "多路并行请使用 parallel 节点，条件分支请使用 decision/ai_decision/confirm。"
                )

        # 可达性提示：仅当存在显式边时给出（legacy 顺序模板跳过）
        reachable = _compute_reachable(pipeline)
        start = pipeline.get_start_node()
        if start is not None:
            unreachable = [n for n in pipeline.nodes if n.id not in reachable]
            if unreachable:
                errors.append(
                    "[警告] 从入口节点出发无法到达: "
                    + ", ".join(n.id for n in unreachable)
                    + "。运行时这些节点将被跳过，请检查连线或补充分支路由。"
                )

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
