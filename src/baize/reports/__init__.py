"""白泽报告管理模块 (Baize Reports)。

提供：
- 内置报告模板（渗透测试报告 / CTF Writeup / 漏洞利用详细报告）
- 报告持久化与管理（预览 / 下载 / 删除）
- agent 工具：list_report_templates / start_report / append_report_section /
  finish_report，支持分段生成落盘，避免超长内容一次性写入失败
"""

from baize.reports.templates import (
    ReportTemplate,
    get_builtin_templates,
    get_template_by_id,
    pick_template,
)
from baize.reports.store import ReportRecord, ReportStore, get_report_store
from baize.reports.api import register

__all__ = [
    "ReportTemplate",
    "get_builtin_templates",
    "get_template_by_id",
    "pick_template",
    "ReportRecord",
    "ReportStore",
    "get_report_store",
    "register",
]
