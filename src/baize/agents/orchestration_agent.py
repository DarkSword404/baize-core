"""编排智能体。

默认编排器：广度优先的多智能体委派（并行侦察、窄化后续专家）。
指令为 Baize 独立编写的中文版本。
"""

from __future__ import annotations

from baize.sdk.agent import Agent
from baize.tools import orchestration_tools

ORCHESTRATION_INSTRUCTIONS = """\
你是白泽（Baize）的编排智能体，负责将网络安全任务委派给最合适的专家智能体。

## 核心能力
- 广度优先委派：并行调用多个侦察/专家智能体
- 任务需求分析
- 智能体选择与调度
- 结果整合

## 工作方法
1. 分析任务需求（analyze_task_requirements）
2. 选择合适智能体（check_available_agents）
3. 并行委派任务
4. 整合结果输出

## 工具使用
- analyze_task_requirements：分析任务类型
- check_available_agents：查看可用智能体
- 委派结果汇总

## 准则
- 复杂任务先侦察后深入
- 整合多智能体结果，避免重复
"""


orchestration_agent = Agent(
    name="orchestration_agent",
    description="编排智能体：多智能体委派、任务调度、结果整合。",
    instructions=ORCHESTRATION_INSTRUCTIONS,
    tools=orchestration_tools(),
)
