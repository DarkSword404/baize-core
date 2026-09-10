"""人工输入测试流水线 — 用于验证「人工输入启动 + 人工确认恢复」链路。

流程：人工输入文本 → blue_team_agent 研判 → 人工确认（approve/reject）→ 汇总。
不调用任何外部工具，纯文本分析，适合端到端验证。
"""

HUMAN_INPUT_TEST_TEMPLATE = {
    "id": "human_input_test",
    "name": "人工输入测试",
    "type": "manual",
    "description": "人工输入告警文本启动，智能体研判后由人工确认，输出汇总结论。",
    "category": "测试",
    "tags": ["测试", "人工输入", "确认"],
    "triggers": ["manual"],
    "context_schema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    "nodes": [
        {
            "id": "analyze",
            "type": "agent",
            "agent": "blue_team_agent",
            "display_name": "告警研判",
            "prompt_template": (
                "请对以下安全告警进行研判，输出：风险等级（low/medium/high/critical）、"
                "影响主机、初步判断。禁止调用任何工具、命令或扫描，仅做纯文本分析。\n\n"
                "告警内容：{{ context.text }}"
            ),
        },
        {
            "id": "confirm",
            "type": "confirm",
            "display_name": "人工确认",
            "confirm_prompt": "是否确认按研判结果执行处置？",
            "confirm_options": ["approve", "reject"],
            "confirm_branches": {
                "approve": "finish",
                "reject": "finish",
            },
        },
        {
            "id": "finish",
            "type": "transform",
            "agent": "report_summary",
            "display_name": "汇总",
            "prompt_template": "",
        },
    ],
}
