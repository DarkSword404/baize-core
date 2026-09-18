"""
预置流水线模板。

type=auto（可激活长驻静默运行）: soc_triage(SOC告警研判) / pentest / vuln_scan / smoke_test
type=manual（对话触发）: phishing_analysis / human_input_test
用户可通过 API 删除内置模板，已删除的模板不在列表中返回。
"""

from baize.orchestration.templates.soc_triage import SOC_TRIAGE_TEMPLATE
from baize.orchestration.templates.pentest import PENTEST_TEMPLATE
from baize.orchestration.templates.vuln_scan import VULN_SCAN_TEMPLATE
from baize.orchestration.templates.phishing_analysis import PHISHING_TEMPLATE
from baize.orchestration.templates.smoke_test import SMOKE_TEST_TEMPLATE
from baize.orchestration.templates.human_input_test import HUMAN_INPUT_TEST_TEMPLATE

_BUILTIN = [
    SOC_TRIAGE_TEMPLATE,       # manual — SOC 告警研判
    PENTEST_TEMPLATE,           # auto   — 自动化渗透测试
    VULN_SCAN_TEMPLATE,         # auto   — 自动化漏洞扫描
    PHISHING_TEMPLATE,          # manual — 钓鱼邮件分析
    SMOKE_TEST_TEMPLATE,        # auto   — 冒烟测试（极简）
    HUMAN_INPUT_TEST_TEMPLATE,  # manual — 人工输入测试（研判+确认）
]


def get_builtin_templates() -> list[dict]:
    """返回内置模板列表，自动跳过已删除的模板。"""
    from baize.api.custom_agents import get_deleted_store
    deleted = get_deleted_store()
    result = []
    for t in _BUILTIN:
        tid = t.get("id", "")
        if deleted.is_template_deleted(tid):
            continue
        result.append(dict(t))
    return result


def get_template_by_id(template_id: str, skip_deleted: bool = True) -> dict | None:
    """按 ID 查找模板。"""
    if skip_deleted:
        from baize.api.custom_agents import get_deleted_store
        deleted = get_deleted_store()
        if deleted.is_template_deleted(template_id):
            return None
    for t in _BUILTIN:
        if t["id"] == template_id:
            return dict(t)
    return None
