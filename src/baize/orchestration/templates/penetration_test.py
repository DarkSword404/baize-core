"""
渗透测试流水线模板。

场景：
    对目标资产执行标准渗透测试流程：侦察 → Web 渗透 → 红队利用 → 报告输出
"""

PENTEST_TEMPLATE = {
    "id": "penetration_test",
    "name": "渗透测试流水线",
    "description": "标准渗透测试流程：侦察 → Web 渗透 → 红队利用 → 报告",
    "category": "安全测试",
    "tags": ["渗透测试", "红队", "Web安全", "侦察"],
    "trigger": {
        "type": "manual",
        "description": "用户输入目标信息后手动触发",
    },
    "context_schema": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "目标地址或域名"},
            "scope": {"type": "string", "description": "测试范围说明"},
        },
    },
    "steps": [
        {
            "id": "recon",
            "type": "agent",
            "agent": "recon_agent",
            "prompt": (
                "对目标进行信息收集和侦察：\n"
                "目标：{{ context.target }}\n"
                "范围：{{ context.scope }}\n\n"
                "请完成以下任务：\n"
                "1. 域名信息收集（Whois、DNS 记录）\n"
                "2. 子域名枚举\n"
                "3. 端口扫描和服务识别\n"
                "4. 技术栈指纹识别"
            ),
        },
        {
            "id": "web_pentest",
            "type": "agent",
            "agent": "web_pentester_agent",
            "prompt": (
                "对侦察阶段发现的目标执行 Web 渗透测试：\n"
                "目标：{{ context.target }}\n"
                "侦察结果：{{ steps.recon.result }}\n\n"
                "请完成以下任务：\n"
                "1. Web 漏洞扫描（SQL注入、XSS、CSRF 等）\n"
                "2. 目录枚举\n"
                "3. 认证绕过测试\n"
                "4. 敏感信息泄露检查"
            ),
        },
        {
            "id": "redteam",
            "type": "agent",
            "agent": "redteam_agent",
            "prompt": (
                "根据前面阶段的发现，尝试漏洞利用和权限提升：\n"
                "目标：{{ context.target }}\n"
                "Web渗透结果：{{ steps.web_pentest.result }}\n\n"
                "请完成以下任务：\n"
                "1. 对确认的漏洞进行无害化利用验证\n"
                "2. 评估影响范围\n"
                "3. 给出修复建议"
            ),
        },
        {
            "id": "done",
            "type": "end",
        },
    ],
}
