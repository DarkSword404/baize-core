"""
SOC 告警研判流水线 (v2 — 接收器模式)
Receiver(数据接收器) → DataTransformer(Syslog解析) → Agent(助手研判)
type: manual — 需要关联外部接收器
"""

SOC_TRIAGE_TEMPLATE = {
    "id": "soc_triage",
    "name": "SOC告警研判",
    "type": "manual",
    "description": "通过数据接收器接收 Syslog/Webhook 告警，解析后送 AI 研判分析",
    "category": "SOC",
    "tags": ["soc", "triage", "alert", "syslog"],
    "triggers": ["manual"],
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
            "agent": "soc_analyst",
            "prompt_template": (
                "你是一个 SOC 安全分析助手。请对输入的安全告警进行分析:\n"
                "1. 提取关键信息（源IP、目标IP、攻击类型、时间）\n"
                "2. 评估告警严重程度（低/中/高/严重）\n"
                "3. 给出推荐处置建议\n"
                "4. 标记是否需要人工介入"
            ),
        },
    ],
    "edges": [
        {"source": "receiver", "target": "datatransformer"},
        {"source": "datatransformer", "target": "agent", "label": "parsed"},
    ],
}
