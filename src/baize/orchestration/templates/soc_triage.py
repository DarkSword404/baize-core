"""
SOC 告警研判流水线 (v3 — 静默会话模式)
Receiver(收件箱) → DataTransformer(Syslog解析) → Agent(助手研判)
type: auto — 激活并绑定数据接收器后，长驻会话逐条静默研判（无需前端交互）
"""

SOC_TRIAGE_TEMPLATE = {
    "id": "soc_triage",
    "name": "SOC告警研判",
    "type": "auto",
    "description": "通过数据接收器接收 Syslog/Webhook 告警，解析后送 AI 静默研判分析（持续批量）",
    "category": "SOC",
    "tags": ["soc", "triage", "alert", "syslog"],
    "triggers": ["auto"],
    "context_schema": None,
    "nodes": [
        {
            "id": "receiver",
            "type": "receiver",
            "display_name": "告警接收器",
            "description": "数据接收器 — 从绑定的 Syslog/Webhook 接收器拉取告警数据",
            "agent": "",  # 由用户在设置中绑定接收器
        },
        {
            "id": "datatransformer",
            "type": "datatransformer",
            "display_name": "Syslog 解析",
            "description": "数据转换器 — 将原始 Syslog 解析为结构化告警",
            "agent": "syslog_parse",
        },
        {
            "id": "agent",
            "type": "agent",
            "display_name": "告警研判",
            "description": "AI 分析告警严重程度并给出处置建议",
            "agent": "blue_team_agent",
            "prompt_template": (
                "你是一个 SOC 安全分析助手。以下是数据接收器转发并经解析的一条待研判安全告警（JSON，含原始内容与解析出的时间/主机/消息等字段）：\n"
                "{{ steps.datatransformer.data | tojson }}\n"
                "请对该告警完成结构化研判：\n"
                "1. 提取关键信息（源IP、目标IP、攻击类型/特征、发生时间）\n"
                "2. 评估告警严重程度（低/中/高/严重）并给出理由\n"
                "3. 判断为真实攻击还是误报，并给出置信度\n"
                "4. 给出推荐处置建议（阻断/隔离/调查/忽略等）\n"
                "5. 标记是否需要人工介入"
            ),
        },
        {
            "id": "end",
            "type": "end",
            "display_name": "结束对话",
            "description": "研判完成，回收该条告警对应的对话（保留运行摘要）",
            "save_dialog": False,
        },
    ],
    "edges": [
        {"source": "receiver", "target": "datatransformer"},
        {"source": "datatransformer", "target": "agent", "label": "parsed"},
        {"source": "agent", "target": "end", "label": "done"},
    ],
}
