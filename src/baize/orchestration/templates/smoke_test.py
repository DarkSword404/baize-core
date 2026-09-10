"""
冒烟测试流水线 — type=auto。

极简结构：单个 Agent 节点，纯文本分析，不触发任何外部工具/命令，
用于快速验证「提交 run → Agent 执行 → report 回填 → completed」全链路，
避免 recon/nmap 等重扫描导致 run 卡死。
"""

SMOKE_TEST_TEMPLATE = {
    "id": "smoke_test",
    "name": "冒烟测试（极简）",
    "type": "auto",
    "description": "极简端到端冒烟测试：单个 Agent 节点完成纯文本分析，不调用任何工具",
    "category": "测试",
    "tags": ["测试", "冒烟", "smoke"],
    "triggers": ["manual"],
    "context_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "待分析的文本内容"},
        },
        "required": ["text"],
    },
    "nodes": [
        {
            "id": "analyze",
            "type": "agent",
            "agent": "blue_team_agent",
            "display_name": "文本分析",
            "description": "基于给定文本直接产出结论，不调用任何工具",
            "prompt_template": (
                "你是一名安全分析师。请基于以下文本直接进行分析总结。\n\n"
                "文本：{{ context.text }}\n\n"
                "要求：\n"
                "1. 仅基于提供的文本进行分析，禁止调用任何工具、命令、扫描或外部查询。\n"
                "2. 直接返回 JSON，格式如下：\n"
                "   {\"summary\": \"一句话总结\", \"findings\": [\"发现1\", \"发现2\"], \"severity\": \"low|medium|high\"}\n"
            ),
        },
        {
            "id": "end",
            "type": "end",
            "display_name": "结束对话",
            "description": "冒烟测试收尾，回收本次测试对应的对话（保留运行摘要）",
            "save_dialog": False,
        },
    ],
}
