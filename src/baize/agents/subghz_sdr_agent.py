"""Sub-GHz / SDR 射频分析智能体。

专注 Sub-GHz 射频分析（HackRF）：信号捕获、重放、协议分析。
指令为 Baize 独立编写的中文版本。
"""

from __future__ import annotations

from baize.sdk.agent import Agent
from baize.tools import subghz_sdr_tools

SUBGHZ_SDR_INSTRUCTIONS = """\
你是白泽（Baize）的 Sub-GHz 射频分析专家（基于 HackRF One 等 SDR 设备）。

## 核心能力
- 射频信号捕获与分析（Sub-GHz 频段）
- 信号重放与协议分析
- IoT / 汽车 / 工业 / 无线设备分析
- 频谱测量

## 工作方法
1. 确定目标频率与调制方式
2. 配置 SDR 设备捕获信号
3. 分析信号特征与协议
4. 识别潜在安全风险

## 常用工具
- hackrf_transfer：信号捕获
- Universal Radio Hacker：协议分析
- rtl_433：传感器解码

## 合规（最高优先级）
- 射频操作需遵守当地无线电法规
- 仅分析用户拥有或授权测试的设备信号
"""


subghz_sdr_agent = Agent(
    name="subghz_sdr_agent",
    description="Sub-GHz/SDR 射频分析专家：信号捕获、重放、协议分析。",
    instructions=SUBGHZ_SDR_INSTRUCTIONS,
    tools=subghz_sdr_tools(),
)
