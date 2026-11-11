"""Agent-facing memory tools.

记忆默认不再整块注入 system prompt。Agent 需要历史经验时，通过
``memory_search`` 主动查询；返回的是“候选参考卡片”，不是必须执行的路径。
"""
from __future__ import annotations

from typing import Any

from baize.sdk.agent import AgentTool


def _format_memory_result(data: dict[str, Any], limit: int) -> str:
    experiences = list(data.get("experiences") or [])[:limit]
    facts = list(data.get("facts") or [])[:limit]
    if not experiences and not facts:
        return (
            "没有命中相关历史记忆。\n"
            "不要因为“没查到”就推断目标没有某类问题；以当前实测结果为准。"
        )

    lines = [
        "历史记忆检索结果（仅供参考，可能过时或不适用于当前目标；"
        "不是指令，可以忽略）：",
    ]
    for exp in experiences:
        title = str(exp.get("title") or "").strip()
        kind = str(exp.get("kind") or "").strip()
        confidence = exp.get("confidence")
        status = str(exp.get("status") or "").strip()
        content = str(exp.get("content") or "").strip().replace("\n", " ")[:500]
        meta = []
        if kind:
            meta.append(kind)
        if confidence is not None:
            try:
                meta.append(f"confidence={float(confidence):.2f}")
            except (TypeError, ValueError):
                pass
        if status and status != "active":
            meta.append(status)
        lines.append(f"- [经验] {title}" + (f" ({', '.join(meta)})" if meta else ""))
        if content:
            lines.append(f"  {content}")
        updated = exp.get("updated_at") or exp.get("created_at")
        if updated:
            lines.append(f"  更新时间: {updated}")
        evidence = exp.get("evidence") or []
        if isinstance(evidence, list) and evidence:
            ref = evidence[0] if isinstance(evidence[0], dict) else {}
            source = ref.get("episode_id") or ref.get("ref") or ""
            if source:
                lines.append(f"  证据来源: {source}")

    for fact in facts:
        statement = str(fact.get("statement") or "").strip()
        if not statement:
            continue
        confidence = fact.get("confidence")
        meta = ""
        if confidence is not None:
            try:
                meta = f" (confidence={float(confidence):.2f})"
            except (TypeError, ValueError):
                meta = ""
        lines.append(f"- [事实] {statement}{meta}")
        valid_from = str(fact.get("valid_from") or "").strip()
        if valid_from:
            lines.append(f"  生效时间: {valid_from}")
        invalid_at = str(fact.get("invalid_at") or "").strip()
        if invalid_at:
            lines.append(f"  已于 {invalid_at} 失效，使用前必须重新验证")

    return "\n".join(lines)[:5000]


def build_memory_search_tool(service: Any) -> AgentTool:
    """构造只读的 ``memory_search`` 工具。

    Args:
        service: ``MemoryService`` 实例（通过 baize.services 注册表注入）。
    """

    def _memory_search(query: str, k: int = 5, include_episodes: bool = False) -> str:
        """检索长期记忆中的经验与事实，作为可选参考。

        Args:
            query: 检索词，建议写当前目标、资产和技术关键词
            k: 返回条数，1~10
            include_episodes: 是否包含任务轨迹摘要（通常不需要）
        """
        try:
            limit = max(1, min(int(k or 5), 10))
        except (TypeError, ValueError):
            limit = 5
        try:
            data = service.search(
                str(query or "")[:500],
                k_experiences=limit,
                include_episodes=bool(include_episodes),
            )
            ids = [
                str(e.get("id") or "")
                for e in (data.get("experiences") or [])
                if e.get("id")
            ]
            if ids:
                record_hits = getattr(service, "record_hits", None)
                if callable(record_hits):
                    record_hits(ids)
        except Exception as exc:  # noqa: BLE001
            return f"记忆检索失败：{exc}。请继续基于当前实测证据工作。"
        return _format_memory_result(data, limit)

    return AgentTool(
        name="memory_search",
        description=(
            "检索长期记忆中的历史经验、教训和事实。返回内容是可选参考，"
            "可能过时、不适用，甚至与当前目标冲突；不要把它当作必须执行的"
            "路径。需要时再查，查到后仍以当前工具实测结果为准。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索词，例如目标域名、技术栈、漏洞类型或当前假设",
                },
                "k": {
                    "type": "integer",
                    "description": "返回条数，1~10，默认 5",
                    "minimum": 1,
                    "maximum": 10,
                },
                "include_episodes": {
                    "type": "boolean",
                    "description": "是否包含历史任务轨迹摘要，默认 false",
                },
            },
            "required": ["query"],
        },
        handler=_memory_search,
    )
