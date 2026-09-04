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


def _run_shell(command: str, timeout: int = 120, **kwargs: Any) -> str:
    """执行 shell 命令并返回输出（经统一执行器抽象）。

    注意：``**kwargs`` 用于容忍模型偶尔产出的 schema 外多余字段
    （如 ``interactive=True``、``session_id="..."``），避免 TypeError
    导致工具直接报「执行失败」。

    **执行器抽象**：走 ``baize.executors`` 的统一后端，不再裸调
    ``subprocess.run``。因此 generic_linux_command 也能享受:
    - ``BAIZE_EXEC_BACKEND=tmux`` 的长任务会话（超时不丢结果）；
    - 进程组级清理（超时/取消时子孙进程不泄漏成孤儿）；
    - 按 ``BAIZE_EXEC_*`` 环境变量可配置的隔离/远程后端。

    注意 ``timeout<=0`` 表示不限制，交由执行器/Agent 兜底超时管理。
    """
    try:
        from baize.executors import ExecutorConfig, build_executor

        cfg = ExecutorConfig.from_env()
        executor = build_executor(cfg)
        result = asyncio.run(executor.run(command, timeout=timeout if timeout and timeout > 0 else 0))
        text = result.text
        if result.session:
            # 长任务仍在后台 tmux 会话中运行，把会话名与输出文件带给模型，
            # 便于稍后用 generic_linux_command 取回完整结果或确认结束。
            log_path = f"/tmp/baize-tmux/{result.session}/output.log"
            text = (
                f"{text}\n[长任务] 命令超过单次执行时限后仍在后台 tmux 会话 "
                f"`{result.session}` 中运行（不会被杀死）。"
                f"输出实时写入 {log_path}；可稍后用 generic_linux_command "
                f"执行 `tail -n 50 {log_path}` 取回结果，或 `tmux kill-session -t "
                f"{result.session}` 终止。命令完成时该会话自动结束。"
            )
        return text
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
    """SSRF 防护：校验 URL 目标是否允许访问；不允许时抛出 ValueError。

    **兼容层**：核心逻辑已统一迁移到 ``baize.agents.guardrails.check_ssrf``
    （安全护栏的 SSRF 子项），支持细粒度开关 + CIDR/域名白名单，可在
    护栏面板或 guardrails.json 中为内网渗透等场景精细放行。

    参数 ``allow_internal=True`` 等价临时绕过（``BAIZE_FETCH_ALLOW_INTERNAL=1``），
    不再做任何检查，用于已知安全的 fetch 内部路径。
    """
    if allow_internal:
        return
    from baize.agents.guardrails import check_ssrf

    ok, msg, _rid = check_ssrf(url)
    if not ok:
        raise ValueError(msg)


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
    """分析任务需求，返回建议的智能体 AGENT_KEY（可通过 check_available_agents 查看全部可用项）。

    返回的标识符均为当前注册表中真实存在的智能体 key/别名，
    可直接交给路由/编排层调用，避免历史硬编码导致指向已不存在的智能体。
    """
    task_l = (task or "").lower()
    mapping = [
        (("web", "注入", "xss", "csrf", "upload", "sql"), "web_pentester"),
        (("取证", "forensic", "dfir", "内存", "磁盘"), "dfir_agent"),
        (("扫描", "recon", "侦察", "端口", "枚举", "网络"), "network_analyzer"),
        (("红队", "redteam", "提权", "exploit", "攻击", "apt"), "red_team_agent"),
        (("蓝队", "blueteam", "应急", "响应"), "blue_team_agent"),
        (("ctf", "flag", "夺旗"), "ctf_agent"),
        (("合规", "compliance", "审计"), "compliance_agent"),
        (("报告", "report", "总结"), "reporting_agent"),
        (("无线", "wifi", "wlan"), "wifi_security_agent"),
        (("android", "移动", "apk"), "android_sast"),
        (("dns", "smtp", "邮件", "mail"), "dns_smtp_agent"),
        (("逆向", "reverse", "反汇编"), "reverse_engineering_agent"),
        (("射频", "subghz", "sdr", "重放"), "replay_attack_agent"),
        (("内存", "memory", "volatility"), "memory_analysis_agent"),
    ]
    fallback = "triage_agent"
    for keywords, key in mapping:
        if any(k in task_l for k in keywords):
            return key
    return fallback


def _check_available_agents() -> str:
    """列出可用智能体（动态从注册表读取，避免硬编码过期列表）。"""
    try:
        from baize.agents import list_agents

        agents = list_agents()
        if not agents:
            return "(无已注册智能体)"
        names = [a.get("name", "") for a in agents]
        return "可用智能体: " + ", ".join(n for n in names if n)
    except Exception as e:  # noqa: BLE001 - 工具调用需返回可读错误而非抛出
        return f"(读取智能体列表失败: {e})"


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
