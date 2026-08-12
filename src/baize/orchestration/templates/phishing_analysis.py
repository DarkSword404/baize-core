"""
钓鱼邮件分析流水线 — type: manual（需要人工确认）。

流程：
    邮件提交 → header_analysis(邮件头分析) → content_scan(内容检测) → parallel(并行分析)
    → correlation(关联研判) → decision → confirm → response(处置) → done
"""

PHISHING_TEMPLATE = {
    "id": "phishing_analysis",
    "name": "钓鱼邮件智能分析",
    "type": "manual",
    "description": "对可疑邮件进行多维度分析，研判是否为钓鱼邮件并自动处置",
    "category": "安全运营",
    "tags": ["钓鱼", "邮件", "威胁情报", "Office365"],
    "triggers": ["webhook", "manual"],
    "context_schema": {
        "type": "object",
        "properties": {
            "email_id": {"type": "string", "description": "邮件ID"},
            "sender": {"type": "string", "description": "发件人地址"},
            "subject": {"type": "string", "description": "邮件主题"},
            "body": {"type": "string", "description": "邮件正文"},
            "headers": {"type": "string", "description": "邮件头原文"},
            "attachments": {"type": "array", "items": {"type": "string"}, "description": "附件列表"},
        },
    },
    "nodes": [
        {
            "id": "header_analysis",
            "type": "agent",
            "agent": "SecurityAnalyst",
            "display_name": "邮件头分析",
            "description": "分析 SPF/DKIM/DMARC 认证和传输路径",
            "prompt_template": (
                "你是一名邮件安全分析师。请分析以下邮件头：\n\n"
                "{{ context.headers }}\n\n"
                "检查：\n"
                "1. SPF/DKIM/DMARC 认证状态\n"
                "2. 发件域和实际发送源是否匹配\n"
                "3. 传输路径中是否有可疑跳数\n\n"
                "返回 JSON 分析结果。"
            ),
        },
        {
            "id": "content_scan",
            "type": "agent",
            "agent": "SecurityAnalyst",
            "display_name": "内容检测",
            "description": "分析邮件正文和链接",
            "prompt_template": (
                "请分析以下邮件内容是否包含钓鱼特征：\n\n"
                "主题：{{ context.subject }}\n"
                "发件人：{{ context.sender }}\n"
                "正文：{{ context.body }}\n"
                "附件：{{ context.attachments }}\n\n"
                "检查：\n"
                "1. 紧急/恐吓性语言\n"
                "2. 可疑链接（域名仿冒、短链接）\n"
                "3. 请求敏感信息\n"
                "4. 附件安全性（宏、恶意扩展名）\n\n"
                "返回 JSON 分析结果，包含 phishing_score(0-1)。"
            ),
        },
        {
            "id": "parallel_check",
            "type": "parallel",
            "display_name": "并行分析",
            "description": "威胁情报查询 + URL 检测 + 附件沙箱",
            "merge_strategy": "all",
            "parallel_branches": [
                {
                    "node_id": "threat_intel",
                    "node": {
                        "id": "threat_intel",
                        "type": "agent",
                        "agent": "ThreatIntel",
                        "display_name": "威胁情报查询",
                        "prompt_template": (
                            "请对以下发件人、域名和 IP 进行威胁情报查询：\n"
                            "发件人：{{ context.sender }}\n"
                            "邮件头分析：{{ steps.header_analysis.output }}\n\n"
                            "查询恶意活动记录、信誉分数、关联攻击组织。"
                        ),
                    },
                },
                {
                    "node_id": "url_check",
                    "node": {
                        "id": "url_check",
                        "type": "agent",
                        "agent": "SecurityAnalyst",
                        "display_name": "URL 检测",
                        "prompt_template": (
                            "检测邮件正文中的 URL 安全性。\n"
                            "内容检测结果：{{ steps.content_scan.output }}"
                        ),
                    },
                },
            ],
        },
        {
            "id": "correlation",
            "type": "agent",
            "agent": "CorrelationAgent",
            "display_name": "关联研判",
            "description": "综合所有分析结果做最终研判",
            "prompt_template": (
                "请综合以下所有分析结果给出最终钓鱼邮件判定：\n\n"
                "邮件头分析：{{ steps.header_analysis.output }}\n"
                "内容检测：{{ steps.content_scan.output }}\n"
                "并行检测：{{ steps.parallel_check.output }}\n\n"
                "返回 JSON：\n"
                "{\n"
                '  "is_phishing": true/false,\n'
                '  "confidence": 0-1,\n'
                '  "phishing_type": "credential_harvesting/business_email_compromise/malware_delivery/not_phishing",\n'
                '  "evidence": ["证据1", "证据2"],\n'
                '  "recommended_action": "quarantine/delete/monitor/no_action"\n'
                "}"
            ),
        },
        {
            "id": "decision",
            "type": "decision",
            "display_name": "钓鱼判定",
            "description": "根据研判结果决定下一步动作",
            "branches": [
                {
                    "when": "{{ steps.correlation.data.is_phishing == True and steps.correlation.data.confidence > 0.8 }}",
                    "goto": "auto_response",
                    "label": "确认钓鱼(高置信)",
                },
                {
                    "when": "{{ steps.correlation.data.is_phishing == True }}",
                    "goto": "human_confirm",
                    "label": "疑似钓鱼(需确认)",
                },
                {"default": True, "goto": "done", "label": "安全邮件"},
            ],
        },
        {
            "id": "auto_response",
            "type": "agent",
            "agent": "IncidentResponder",
            "display_name": "自动处置",
            "description": "高置信钓鱼邮件自动处置",
            "prompt_template": (
                "自动处置钓鱼邮件，执行：\n"
                "1. 将邮件移至隔离区\n"
                "2. 删除相同来源邮件\n"
                "3. 上报威胁情报平台\n"
                "邮件：{{ context.email_id }}"
            ),
        },
        {
            "id": "human_confirm",
            "type": "confirm",
            "display_name": "人工确认",
            "description": "低置信钓鱼邮件需要人工判定",
            "confirm_prompt": (
                "⚠️ 低置信度钓鱼邮件需要人工判定\n\n"
                "发件人：{{ context.sender }}\n"
                "主题：{{ context.subject }}\n"
                "置信度：{{ steps.correlation.data.confidence }}\n"
                "证据：{{ steps.correlation.data.evidence }}\n\n"
                "请选择：\n"
                "- approve: 确认钓鱼，执行处置\n"
                "- reject: 标记误报"
            ),
            "confirm_options": ["approve", "reject"],
            "confirm_branches": {"approve": "auto_response", "reject": "done"},
        },
        {
            "id": "done",
            "type": "transform",
            "agent": "report_summary",
            "display_name": "汇总",
            "prompt_template": "",
        },
    ],
}
