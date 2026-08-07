"""Android 静态应用安全测试（SAST）智能体。

专注 Android 应用安全分析：应用逻辑映射、静态漏洞发现。
指令为 Baize 独立编写的中文版本。
"""

from __future__ import annotations

from baize.sdk.agent import Agent
from baize.tools import android_sast_tools

APP_LOGIC_MAPPER_INSTRUCTIONS = """\
你是白泽（Baize）的 Android 应用逻辑分析专家。

## 核心能力
- APK 结构分析
- 应用逻辑流程映射
- 权限与组件分析
- 数据流追踪

## 常用工具
- apktool：APK 解码
- jadx：反编译
- aapt：Android 资源工具

## 合规
- 仅分析用户授权或有合法权利的 APK
"""


android_sast_agent = Agent(
    name="android_sast_agent",
    description="Android 静态应用安全测试：应用逻辑分析、SAST 漏洞发现。",
    instructions=APP_LOGIC_MAPPER_INSTRUCTIONS,
    tools=android_sast_tools(),
)
