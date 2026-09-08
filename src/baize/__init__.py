"""Baize (白泽·智脑) — 一个开源的 AI 网络安全框架。

本项目是一个独立的 AI 网络安全自动化框架，采用浏览器/服务器（B/S）架构，
通过 Web 界面提供对话、智能体编排、漏洞分析等能力。
"""

import pkgutil

__version__ = "2.0.1"

# baize 同时作为可扩展的顶层命名空间：扩展发行版（如 baize-orchestration 提供的
# baize.orchestration 子包）与本包常分属不同源码树 / 安装位置。若不合并，顶层包一旦
# 命中本目录的 __init__.py（普通包），同 sys.path 上其余 baize 命名空间子包将不可见；
# extend_path 把这些子包目录并入 __path__，使 pip install -e / 源码 PYTHONPATH 混用下
# import baize.orchestration 依然可用。
__path__ = pkgutil.extend_path(__path__, __name__)
