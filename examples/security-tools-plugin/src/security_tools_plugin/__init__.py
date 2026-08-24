"""Baize 安全工具插件示例。

演示三件事：
1. 标准 Tool 协议 —— 用 ``@register_tool`` 声明式注册工具；
2. 动态注册机制 —— 通过 entry point（``baize.tools`` 组）被自动发现；
3. 执行环境抽象 —— 工具内不直接调 subprocess，而是走 ``build_executor``，
   同一工具可切换 local / docker / ssh 执行后端。

本插件提供两个无害的侦查类工具（证书透明度查询 / robots 探测），
仅用于演示插件开发模式，请勿对未授权目标使用。
"""

from __future__ import annotations

import asyncio
import json

from baize.executors import build_executor
from baize.tools import register_tool

__all__ = ["register", "crt_lookup", "robots_probe"]


def _run(command: str, timeout: int = 60) -> str:
    """通过执行器抽象执行命令（默认 local，可被 BAIZE_EXEC_* 环境变量切换）。"""
    executor = build_executor()
    return asyncio.run(executor.run(command, timeout=timeout)).text


@register_tool(
    name="crt_lookup",
    description="通过 crt.sh 证书透明度日志查询域名的历史证书（子域名枚举辅助）。"
    "参数 target: 目标域名，如 example.com",
    category="recon",
    tags=["osint", "subdomain"],
)
def crt_lookup(target: str) -> str:
    """查询域名的证书透明度记录。"""
    # 用 curl 查询 crt.sh 的 JSON 接口
    command = (
        f"curl -s --max-time 20 "
        f"'https://crt.sh/?q=%25.{target}&output=json' "
        f"| head -c 4000"
    )
    output = _run(command)
    if not output or output == "(exit 0, 无输出)":
        return "(crt.sh 无返回，可能目标不可达或网络受限)"
    return output[:4000]


@register_tool(
    name="robots_probe",
    description="抓取目标站点 robots.txt，判断是否泄漏敏感路径（侦查辅助）。"
    "参数 target: 目标 URL，如 http://example.com",
    category="recon",
    tags=["web", "osint"],
)
def robots_probe(target: str) -> str:
    """抓取 robots.txt 内容。"""
    command = f"curl -s --max-time 20 {target.rstrip('/')}/robots.txt | head -c 3000"
    output = _run(command)
    if not output or output == "(exit 0, 无输出)":
        return f"(未找到 {target}/robots.txt)"
    return output[:3000]


def register(registry) -> None:
    """entry point 入口：被 ``ToolRegistry.discover_entry_points()`` 自动调用。

    使用 ``@register_tool`` 装饰器时不需要手动操作 registry，
    此函数仅作占位说明 —— 更精细的控制（自定义 ToolSpec / 校验参数）
    可以在本函数内手动 ``registry.register(...)``。
    """
    # 示例：手动注册一个不带装饰器的工具
    from baize.tools import ToolSpec

    registry.register(
        ToolSpec(
            name="example_noop",
            description="插件演示工具：什么也不做，返回 ok。",
            parameters={"type": "object", "properties": {}},
            handler=lambda **_: "ok",
            category="utility",
            tags=["demo"],
        )
    )


if __name__ == "__main__":
    # 插件自测：python -m security_tools_plugin
    print(crt_lookup("example.com"))
    print(robots_probe("http://example.com"))
    print(json.dumps({"status": "ok", "tools": ["crt_lookup", "robots_probe", "example_noop"]}))
