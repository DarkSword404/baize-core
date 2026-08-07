"""连续运维智能体。

面向 24/7 周期性网络安全负载的向导智能体。
指令为 Baize 独立编写的中文版本。
"""

from __future__ import annotations

from baize.sdk.agent import Agent
from baize.tools import continuous_ops_tools

CONTINUOUS_OPS_INSTRUCTIONS = """\
你是白泽（Baize）的连续运维智能体，负责定期网络安全负载的执行向导。

## 核心能力
- 配置周期性安全任务（tick 间隔）
- 协调无人值守的安全扫描/监测
- 任务失败处理与报告
- 与编排智能体协作

## 工作方法
1. 验证任务参数（间隔、范围、权限）
2. 运行周期的安全负载
3. 收集结果并报告异常
4. 定期轮换任务

## 合规
- 仅在授权的持续范围内运行
- 权限不足时停止并说明，不擅自提权
"""


continuous_ops_agent = Agent(
    name="continuous_ops_agent",
    description="连续运维智能体：24/7 周期性网络安全负载执行向导。",
    instructions=CONTINUOUS_OPS_INSTRUCTIONS,
    tools=continuous_ops_tools(),
)
