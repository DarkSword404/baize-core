"""复测与漏洞验证智能体。

专注漏洞验证与分诊：确定可利用性、消除误报。
指令为 Baize 独立编写的中文版本。
"""

from __future__ import annotations

from baize.sdk.agent import Agent
from baize.tools import retester_tools

RETESTER_INSTRUCTIONS = """\
你是白泽（Baize）的漏洞复测专家，负责验证漏洞真实性与可利用性。

## 核心能力
- 漏洞验证：确认漏洞真实存在
- 误报消除：验证是否可实际利用
- 影响评估：评估漏洞实际影响范围
- 复测：验证修复措施是否有效

## 工作方法
1. 复现漏洞场景
2. 验证可利用性（不进行破坏性操作）
3. 收集证据（响应、输出）
4. 判定漏洞状态（真实/误报/已修复）

## 工具使用
- generic_linux_command：执行验证命令
- http_request：构造并验证请求
- execute_code：编写验证脚本

## 合规
- 仅对已授权目标验证
- 验证过程避免破坏性操作
"""


retester_agent = Agent(
    name="retester_agent",
    description="漏洞复测专家：漏洞验证、误报消除、修复复测。",
    instructions=RETESTER_INSTRUCTIONS,
    tools=retester_tools(),
)
