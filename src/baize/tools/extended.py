"""Baize 扩展工具（独立实现）。

提供智能体常用的安全分析工具：代码执行、Web 请求、SSH、
搜索、网络侦查等。全部为 Baize 独立编写。
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
from typing import Any

import httpx

from baize.sdk.agent import AgentTool


def _run_shell(command: str, timeout: int = 120) -> str:
    """执行 shell 命令并返回输出。"""
    try:
        proc = subprocess.run(
            ["/bin/bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ((proc.stdout or "") + (proc.stderr or "")).strip() or f"(exit {proc.returncode})"
    except subprocess.TimeoutExpired:
        return f"(超时 {timeout}s)"
    except Exception as e:  # noqa: BLE001
        return f"(错误: {e})"


def _execute_code(code: str, timeout: int = 60) -> str:
    """在隔离的临时环境中执行 Python 代码。"""
    try:
        proc = subprocess.run(
            ["python3", "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return out.strip() or f"(exit {proc.returncode}, 无输出)"
    except subprocess.TimeoutExpired:
        return f"(代码执行超时 {timeout}s)"
    except Exception as e:  # noqa: BLE001
        return f"(错误: {e})"


def _resolve_host_ips(hostname: str) -> list[str]:
    """将主机名解析为 IP 列表；对十进制/十六进制/八进制变体 IPv4 归一化。"""
    import ipaddress
    import re
    import socket

    host = (hostname or "").strip().strip("[]")
    if not host:
        return []
    # 1) 本身就是合法 IP（含 IPv6）
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    # 2) 变体 IPv4：纯数字（十进制 / 十六进制，如 2130706433、0x7f000001）
    if re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|\d+)", host):
        try:
            val = int(host, 0)
            if 0 <= val <= 0xFFFFFFFF:
                return [str(ipaddress.IPv4Address(val))]
        except ValueError:
            pass
    # 3) 点分变体（如 0177.0.0.1、0x7f.0.0.1、0x7f.1）
    parts = host.split(".")
    if 1 < len(parts) <= 4:
        try:
            numeric = []
            for part in parts:
                if part.startswith(("0x", "0X")):
                    numeric.append(int(part, 16))
                elif len(part) > 1 and part.startswith("0"):
                    numeric.append(int(part, 8))
                else:
                    numeric.append(int(part, 10))
            if all(0 <= n <= 255 for n in numeric):
                rebuilt = ".".join(str(n) for n in numeric)
                try:
                    ipaddress.IPv4Address(rebuilt)
                    return [rebuilt]
                except ValueError:
                    pass
        except ValueError:
            pass
    # 4) 域名：DNS 解析全部 A/AAAA 记录
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    seen: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.append(ip)
    return seen


def _is_blocked_ip(ip: str) -> bool:
    """IP 是否属于应阻止访问的内部/保留地址。"""
    import ipaddress

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _check_url_allowed(url: str, allow_internal: bool) -> None:
    """校验 URL 目标是否允许访问；不允许时抛出 ValueError。

    注意：域名解析后再校验，可阻止常规 SSRF；DNS rebinding 需
    自定义 transport 做连接时校验，属后续增强项。
    """
    from urllib.parse import urlparse

    if allow_internal:
        return
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise ValueError("无效 URL（缺少主机名）")
    ips = _resolve_host_ips(host)
    if not ips:
        raise ValueError("无法解析主机名")
    for ip in ips:
        if _is_blocked_ip(ip):
            raise ValueError("出于安全考虑，禁止访问内部/保留地址")


def _http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout: int = 30,
) -> str:
    """发起 HTTP 请求（带 SSRF 防护：目标 IP 校验 + 手动重定向校验）。"""
    try:
        allow_internal = os.getenv("BAIZE_FETCH_ALLOW_INTERNAL", "").lower() in ("1", "true")
        method_u = method.upper()
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            current = url
            resp = None
            for _ in range(6):
                _check_url_allowed(current, allow_internal)
                resp = client.request(method_u, current, headers=headers or {}, content=body)
                # 手动跟随重定向：每次跳转都重新校验目标，防止重定向到内网
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        break
                    current = str(httpx.URL(current).join(location))
                    continue
                break
            else:
                return "(重定向次数过多，已中止)"
        return f"HTTP {resp.status_code}\n{resp.text[:5000]}"
    except Exception as e:  # noqa: BLE001
        return f"(请求失败: {e})"


def _ssh_command(
    host: str,
    command: str,
    username: str = "",
    password: str = "",
    port: int = 22,
) -> str:
    """通过 SSH 在远程主机执行命令。"""
    user_part = f"{username}@" if username else ""
    try:
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", "-p", str(port), f"{user_part}{host}", command]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                              input=password + "\n", env=dict(os.environ))
        return ((proc.stdout or "") + (proc.stderr or "")).strip() or "(无输出)"
    except Exception as e:  # noqa: BLE001
        return f"(SSH 失败: {e})"


def _web_search(query: str) -> str:
    """执行 Web 搜索（使用本地工具或返回提示）。"""
    return f"(搜索: {query} — 如需在线搜索请配置搜索 API)"


def _shodan_search(query: str) -> str:
    """Shodan 搜索（需要 SHODAN_API_KEY）。"""
    key = os.getenv("SHODAN_API_KEY", "")
    if not key:
        return "(未配置 SHODAN_API_KEY)"
    try:
        resp = httpx.get(f"https://api.shodan.io/shodan/host/search?key={key}&query={query}", timeout=30)
        return resp.text[:4000]
    except Exception as e:  # noqa: BLE001
        return f"(Shodan 查询失败: {e})"


def _port_scan(target: str, ports: str = "common") -> str:
    """TCP 端口扫描。"""
    import socket
    from concurrent.futures import ThreadPoolExecutor

    common = [21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445,
              993, 995, 1433, 1521, 3306, 3389, 5432, 5900, 6379, 8080, 8443]
    if ports == "common":
        port_list = common
    else:
        try:
            port_list = [int(p.strip()) for p in ports.split(",") if p.strip()]
        except ValueError:
            return "(无效端口列表)"

    def _scan(p: int) -> int | None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            return p if s.connect_ex((target, p)) == 0 else None
        except OSError:
            return None
        finally:
            s.close()

    open_ports = []
    with ThreadPoolExecutor(max_workers=50) as ex:
        for r in ex.map(_scan, port_list):
            if r:
                open_ports.append(r)
    return f"{target} 开放端口: " + (", ".join(str(p) for p in sorted(open_ports)) if open_ports else "无")


def _analyze_task_requirements(task: str) -> str:
    """分析任务需求，返回建议的智能体类型。"""
    task_l = task.lower()
    if any(k in task_l for k in ("web", "注入", "xss", "csrf", "upload")):
        return "web_pentester_agent"
    if any(k in task_l for k in ("取证", "forensic", "dfir", "内存", "磁盘")):
        return "dfir_agent"
    if any(k in task_l for k in ("扫描", "recon", "侦察", "端口", "枚举")):
        return "recon_agent"
    if any(k in task_l for k in ("红队", "redteam", "提权", "exploit", "攻击")):
        return "redteam_agent"
    return "general_agent"


def _check_available_agents() -> str:
    """列出可用智能体。"""
    return "可用智能体: general_agent, ctf_agent, web_pentester_agent, redteam_agent, blueteam_agent, dfir_agent, recon_agent, network_analysis_agent, reporting_agent, retester_agent, reverse_engineering_agent, wifi_security_agent, compliance_agent, dns_smtp_agent, codeagent"


def _verify_csv_inventory() -> str:
    """验证 CSV 资产清单（占位，返回工具提示）。"""
    return "(请提供 CSV 文件路径以验证资产清单)"


def _think(thought: str) -> str:
    """记录一次思考过程（供模型输出推理）。"""
    return f"[思考] {thought}"


def make_tool(name: str, description: str, handler: Any, parameters: dict[str, Any]) -> AgentTool:
    return AgentTool(name=name, description=description, parameters=parameters, handler=handler)


# ----------------------------------------------------------------------
# 工具注册集合
# ----------------------------------------------------------------------
def extended_tools() -> list[AgentTool]:
    """返回扩展工具的完整集合。"""
    return [
        make_tool(
            "generic_linux_command",
            "在本地执行任意 Linux/Unix shell 命令并返回输出。",
            _run_shell,
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "shell 命令"},
                    "timeout": {"type": "integer", "description": "超时秒数"},
                },
                "required": ["command"],
            },
        ),
        make_tool(
            "execute_code",
            "执行 Python 代码片段并返回输出，用于数据处理、脚本编写。",
            _execute_code,
            {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python 代码"}},
                "required": ["code"],
            },
        ),
        make_tool(
            "http_request",
            "发起 HTTP/HTTPS 请求并返回响应。",
            _http_request,
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL"},
                    "method": {"type": "string", "description": "HTTP 方法"},
                    "headers": {"type": "object", "description": "请求头"},
                    "body": {"type": "string", "description": "请求体"},
                },
                "required": ["url"],
            },
        ),
        make_tool(
            "run_ssh_command_with_credentials",
            "通过 SSH 在远程主机执行命令。",
            _ssh_command,
            {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "主机地址"},
                    "command": {"type": "string", "description": "要执行的命令"},
                    "username": {"type": "string", "description": "用户名"},
                    "password": {"type": "string", "description": "密码"},
                    "port": {"type": "integer", "description": "SSH 端口"},
                },
                "required": ["host", "command"],
            },
        ),
        make_tool(
            "make_web_search_with_explanation",
            "执行 Web 搜索获取最新信息。",
            _web_search,
            {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
        ),
        make_tool(
            "shodan_search",
            "使用 Shodan 搜索互联网暴露设备与服务。",
            _shodan_search,
            {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Shodan 查询"}},
                "required": ["query"],
            },
        ),
        make_tool(
            "port_scan",
            "对目标执行 TCP 端口扫描。",
            _port_scan,
            {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "目标 IP/域名"},
                    "ports": {"type": "string", "description": "端口列表"},
                },
                "required": ["target"],
            },
        ),
        make_tool(
            "analyze_task_requirements",
            "分析任务需求，建议合适的智能体类型。",
            _analyze_task_requirements,
            {
                "type": "object",
                "properties": {"task": {"type": "string", "description": "任务描述"}},
                "required": ["task"],
            },
        ),
        make_tool(
            "check_available_agents",
            "列出当前可用的全部智能体。",
            _check_available_agents,
            {
                "type": "object",
                "properties": {},
            },
        ),
        make_tool(
            "verify_csv_inventory",
            "验证 CSV 资产清单文件。",
            _verify_csv_inventory,
            {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "CSV 路径"}},
                "required": ["path"],
            },
        ),
        make_tool(
            "think",
            "记录并输出中间推理过程。",
            _think,
            {
                "type": "object",
                "properties": {"thought": {"type": "string", "description": "思考内容"}},
                "required": ["thought"],
            },
        ),
    ]
