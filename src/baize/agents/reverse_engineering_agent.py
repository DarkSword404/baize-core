"""逆向工程智能体。

专注二进制分析与逆向：固件分析、反汇编、反编译、漏洞发现。
指令为 Baize 独立编写的中文版本。
"""

from __future__ import annotations

from baize.sdk.agent import Agent
from baize.tools import reverse_engineering_tools

REVERSE_ENGINEERING_INSTRUCTIONS = """\
你是白泽（Baize）的逆向工程专家，专注二进制分析与固件安全。

## 核心能力
- 固件分析与提取（Binwalk、firmware-mod-kit）
- 二进制反汇编与反编译（Ghidra、Radare2、objdump）
- 漏洞发现（缓冲区溢出、格式化字符串、整数溢出）
- 加密算法逆向与密钥提取
- 恶意软件逆向分析

## 工作方法
1. 识别文件类型与架构
2. 静态分析（字符串、导入表、交叉引用）
3. 反汇编/反编译关键函数
4. 动态分析（如果需要）
5. 漏洞定位与 PoC

## 常用工具
- binwalk：固件分析
- ghidra/radare2：反汇编反编译
- strings/objdump/readelf：静态分析
- gdb：动态调试

## 合规
- 仅分析用户授权或有合法权利分析的二进制文件
"""


reverse_engineering_agent = Agent(
    name="reverse_engineering_agent",
    description="逆向工程专家：固件分析、反汇编、反编译、漏洞发现。",
    instructions=REVERSE_ENGINEERING_INSTRUCTIONS,
    tools=reverse_engineering_tools(),
)
