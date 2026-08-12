"""
漏洞扫描报告处理流水线 (v2 — 接收器模式)
Receiver(PDF/HTML报告接收) → DataTransformer(报告解析) → Agent(漏洞分析)
type: auto — 接收器触发后自动执行
"""

VULN_SCAN_TEMPLATE = {
    "id": "vuln_scan",
    "name": "漏洞扫描报告处理",
    "type": "auto",
    "description": "通过数据接收器接收漏扫报告(PDF/HTML)，自动提取漏洞信息后送 AI 分析",
    "category": "漏洞管理",
    "tags": ["vuln", "scan", "report", "pdf", "html"],
    "triggers": ["manual"],
    "context_schema": None,
    "nodes": [
        {
            "id": "receiver",
            "type": "receiver",
            "display_name": "报告接收器",
            "description": "数据接收器 — 从绑定的 Webhook/文件监视器拉取漏扫报告",
            "agent": "",
        },
        {
            "id": "datatransformer",
            "type": "datatransformer",
            "display_name": "报告解析",
            "description": "数据转换器 — 将 PDF/HTML 报告转为纯文本",
            "agent": "html_to_text",
        },
        {
            "id": "agent",
            "type": "agent",
            "display_name": "漏洞分析",
            "description": "AI 分析漏洞信息，按严重程度排序并给出修复建议",
            "agent": "vuln_analyst",
            "prompt_template": (
                "你是一个漏洞分析专家。请对输入的漏扫报告进行分析：\n"
                "1. 提取所有漏洞信息（CVE编号、CVSS评分、影响组件）\n"
                "2. 按严重程度排序（Critical > High > Medium > Low）\n"
                "3. 分析每个漏洞的影响范围\n"
                "4. 给出修复优先级和修复建议\n"
                "5. 生成摘要报告"
            ),
        },
    ],
    "edges": [
        {"source": "receiver", "target": "datatransformer"},
        {"source": "datatransformer", "target": "agent", "label": "parsed"},
    ],
}
