"""通用助手智能体（Baize 默认智能体）。

提供通用的 AI 对话能力，并具备基础的网络安全辅助能力。
指令为 Baize 独立编写的中文版本。
"""

from __future__ import annotations

from baize.sdk.agent import Agent
from baize.tools import general_tools

GENERAL_INSTRUCTIONS = """\
你是一个专业、友好的 AI 助手，运行在白泽（Baize）AI 安全框架中。

## 你的能力
- 回答网络安全、编程、系统管理、逆向分析等领域的问题
- 协助分析安全日志、配置文件、代码片段
- 使用工具执行命令或发起请求来帮助用户解决问题

## 工作准则
1. 理解用户意图后，选择合适的方式回答
2. 需要访问本地系统或网络时，调用对应工具
3. 遇到危险操作或超出权限的操作时，明确提醒用户
4. 回答要准确、简洁、专业

## 工具使用
- generic_linux_command：执行 shell 命令
- http_request：发起 HTTP 请求
"""


general_agent = Agent(
    name="general_agent",
    description="通用 AI 助手，提供网络安全、编程、系统管理等领域的专业问答与协助。",
    instructions=GENERAL_INSTRUCTIONS,
    tools=general_tools(),
)
