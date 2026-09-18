"""报告生成 agent 工具。

四段式工作流（分段落盘，避免超长内容一次性传输失败）：
1. list_report_templates()  — 查看可用模板与章节大纲
2. start_report(title, template_id?) — 创建 draft，返回 report_id 与大纲
3. append_report_section(report_id, section_markdown) — 按章节逐段写入
4. finish_report(report_id) — 标记完成

工具绑定 ReportStore 与当前 session_id，生成的报告自动关联会话，
用户可在「报告管理」页面预览/下载/删除。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from baize.sdk.agent import AgentTool

from baize.reports.templates import (
    get_builtin_templates,
    get_template_by_id,
    pick_template,
)
from baize.reports.store import ReportStore

logger = logging.getLogger("baize.reports.tools")

# 单段内容上限（防止单次工具调用 payload 过大触发模型侧 buffer overflow）
_MAX_SECTION_CHARS = 12000


def build_report_tools(
    report_store: ReportStore,
    session_id: str = "",
) -> list[AgentTool]:
    """构造报告生成工具集合（绑定 store 与会话）。"""

    # ── 1. 列出模板 ──

    def _list_templates() -> str:
        """列出系统内置的报告模板及其适用场景与章节大纲。

        在生成报告前先调用本工具，根据用户要求的报告类型选择合适的
        template_id。
        """
        lines = ["=== 可用报告模板 ==="]
        for t in get_builtin_templates():
            lines.append(f"\n【{t.id}】{t.name}")
            lines.append(f"  适用：{t.description}")
            lines.append(f"  章节：{' / '.join(s.title for s in t.sections)}")
        lines.append(
            "\n选择方法：start_report(title=..., template_id=上述id)；"
            "若不确定模板，template_id 留空由系统自动选择。"
        )
        return "\n".join(lines)

    # ── 2. 开始报告（draft） ──

    def _start_report(title: str, template_id: str = "") -> str:
        """创建一份新报告（草稿状态），返回 report_id 和该模板的章节大纲。

        创建后必须按章节多次调用 append_report_section 逐段写入内容，
        最后调用 finish_report 完成。不要把整份报告塞进一次调用。

        Args:
            title: 报告标题，如「visitor.jushi.com JNDI 注入漏洞利用报告」
            template_id: 模板 id（先调 list_report_templates 查看）；
                留空则根据标题自动选择
        """
        if not title or not title.strip():
            return "(标题不能为空)"
        tpl = get_template_by_id(template_id) if template_id else None
        if tpl is None:
            tpl = pick_template(title)
        try:
            rec = report_store.start_report(
                title=title.strip(),
                template_id=tpl.id,
                template_name=tpl.name,
                session_id=session_id,
            )
            logger.info("agent start_report: %s tpl=%s", rec.id, tpl.id)
            return (
                f"✓ 报告已创建（草稿）\n"
                f"report_id: {rec.id}\n"
                f"模板: {tpl.name}\n\n"
                f"{tpl.outline()}\n\n"
                f"【操作要求】\n"
                f"1. 严格按上述章节顺序，逐章调用 append_report_section；\n"
                f"2. 每次只写 1~2 个章节，内容基于已确认的证据，不得编造；"
                f"没有证据的章节明确标注「未验证/不适用」；\n"
                f"3. 全部章节写完后调用 finish_report。"
            )
        except Exception as e:  # noqa: BLE001
            return f"(创建报告失败: {e})"

    # ── 3. 追加章节 ──

    def _append_section(report_id: str, section_markdown: str) -> str:
        """向草稿报告追加一个章节的 Markdown 内容。

        Args:
            report_id: start_report 返回的 report_id
            section_markdown: 本章节的完整 Markdown（含「## 章节标题」），
                单次不超过约 12000 字符；内容较长的章节可分多次追加
        """
        if not report_id or not section_markdown:
            return "(report_id 和 section_markdown 不能为空)"
        rec = report_store.get(report_id)
        if rec is None:
            return f"(报告不存在: {report_id})"
        if rec.status != "draft":
            return f"(报告 {report_id} 已完成，无法追加)"
        chunk = section_markdown
        truncated = False
        if len(chunk) > _MAX_SECTION_CHARS:
            chunk = chunk[:_MAX_SECTION_CHARS]
            truncated = True
        try:
            report_store.append_section(report_id, chunk)
            size = report_store.get(report_id).size if report_store.get(report_id) else 0
            note = "（注意：本段超长已截断，请将剩余部分再调一次 append）" if truncated else ""
            return f"✓ 已追加章节（报告当前 {size} 字节）{note}"
        except Exception as e:  # noqa: BLE001
            return f"(追加章节失败: {e})"

    # ── 4. 完成报告 ──

    def _finish_report(report_id: str) -> str:
        """完成报告：将草稿标记为正式报告，之后用户即可在报告管理中查看/下载。

        Args:
            report_id: start_report 返回的 report_id
        """
        rec = report_store.finish_report(report_id)
        if rec is None:
            return f"(报告不存在: {report_id})"
        return (
            f"✓ 报告已完成并保存：{rec.title}\n"
            f"report_id: {rec.id}｜模板: {rec.template_name}｜{rec.size} 字节\n"
            f"用户可在「报告管理」页面预览与下载。"
        )

    list_templates_tool = AgentTool(
        name="list_report_templates",
        description=(
            "列出系统内置的报告模板（渗透测试报告/CTF Writeup/漏洞利用详细报告）"
            "及章节大纲。用户要求生成报告时先调用本工具选择模板。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_list_templates,
    )

    start_tool = AgentTool(
        name="start_report",
        description=(
            "开始生成一份正式报告（创建草稿，返回 report_id 与模板章节大纲）。"
            "之后必须按章节多次调用 append_report_section 分段写入，"
            "最后调用 finish_report 完成。适用于用户要求"
            "「详细报告/利用报告/渗透报告/writeup」等交付场景。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "报告标题",
                },
                "template_id": {
                    "type": "string",
                    "description": "模板 id（可选，留空自动选择）："
                                   "vuln_exploit / pentest_report / ctf_writeup",
                    "enum": ["vuln_exploit", "pentest_report", "ctf_writeup", ""],
                },
            },
            "required": ["title"],
        },
        handler=_start_report,
    )

    append_tool = AgentTool(
        name="append_report_section",
        description=(
            "向草稿报告追加一个章节的 Markdown 内容。"
            "每次只写一个章节，内容基于已确认证据，不得编造；"
            "未验证的内容明确标注。超长章节可分多次追加。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "report_id": {"type": "string", "description": "start_report 返回的 report_id"},
                "section_markdown": {
                    "type": "string",
                    "description": "本章节完整 Markdown（含 ## 标题），单次 ≤12000 字符",
                },
            },
            "required": ["report_id", "section_markdown"],
        },
        handler=_append_section,
    )

    finish_tool = AgentTool(
        name="finish_report",
        description=(
            "完成报告：草稿标记为正式报告，用户即可在报告管理页面预览/下载。"
            "所有章节追加完毕后必须调用本工具收尾。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "report_id": {"type": "string", "description": "start_report 返回的 report_id"},
            },
            "required": ["report_id"],
        },
        handler=_finish_report,
    )

    return [list_templates_tool, start_tool, append_tool, finish_tool]
