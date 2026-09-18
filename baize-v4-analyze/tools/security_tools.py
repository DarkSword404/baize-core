"""Baize 安全工具集 — 将外部安全工具封装为标准化 Tool。

通过 :mod:`baize.tools.registry` 的 ``register_tool`` 装饰器注册，
基于 :mod:`baize.executors` 的执行器抽象执行，具备:
- 结构化参数（JSON Schema，LLM 无需猜命令）
- 参数白名单 / 危险参数拦截（防武器化滥用）
- 执行环境隔离（本地 / Docker / SSH 可配置）
- 统一超时与结构化输出

**授权声明**: 以下工具仅用于**已获得授权**的渗透测试 / 安全评估。
平台内置拦截逻辑拒绝明显武器化的用法（如 sqlmap --os-shell）。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from baize.executors import ExecutorConfig, build_executor
from baize.tools.registry import register_tool

logger = logging.getLogger("baize.tools.security")


# ---------------------------------------------------------------------------
# 危险参数拦截
# ---------------------------------------------------------------------------

# 危险参数黑名单（值级拦截，防止授权扫描被滥用为攻击）
_DANGEROUS_PATTERNS: dict[str, list[str]] = {
    "sqlmap": ["--os-shell", "--os-pwn", "--sql-shell", "--file-write", "--file-read"],
    "metasploit": ["-x", "exploit", "shell"],
    "hashcat": [],
}


def _is_weaponized_allowed() -> bool:
    return os.environ.get("BAIZE_ALLOW_WEAPONIZED", "").strip() == "1"


def _check_command(tool_name: str, command: str) -> Optional[str]:
    """检查命令是否包含被拦截的危险参数。

    Returns:
        拦截原因；None 表示放行。
    """
    patterns = _DANGEROUS_PATTERNS.get(tool_name, [])
    for pattern in patterns:
        if pattern in command:
            if _is_weaponized_allowed():
                return None
            return f"检测到危险参数 `{pattern}`，已拦截（除非显式设置 BAIZE_ALLOW_WEAPONIZED=1）"
    return None


async def _run_tool(
    tool_name: str,
    binary: str,
    args: list[str],
    timeout: int = 120,
    config: Optional[ExecutorConfig] = None,
) -> str:
    """构造命令并通过执行器运行（含危险参数校验）。"""
    command = " ".join([binary] + args)
    blocked = _check_command(tool_name, command)
    if blocked:
        return f"[已拦截] {blocked}"
    executor = build_executor(config)
    result = await executor.run(command, timeout=timeout)
    if result.returncode != 0 and not result.timed_out and not result.stdout:
        return f"[{tool_name} 执行失败 exit={result.returncode}] {result.text}"
    return result.text


# ---------------------------------------------------------------------------
# 端口扫描
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 nmap 执行端口扫描 / 服务识别 / 操作系统指纹。"
        "参数: host 目标主机或网段; ports 端口范围(如 '1-1000' 或 '80,443')，"
        "可选; scan_type 扫描类型: tcp_connect(-sT)/syn(-sS)/udp(-sU)/version(-sV)"
        "/os(-O)，默认 tcp_connect; 输出包含开放端口与服务信息。"
    ),
    category="security",
    tags=["network", "scan", "nmap"],
)
async def nmap_scan(
    host: str,
    ports: Optional[str] = None,
    scan_type: str = "tcp_connect",
    timeout: int = 120,
) -> str:
    """nmap 端口扫描工具（封装自 nmap CLI）。"""
    flags_map = {
        "tcp_connect": "-sT",
        "syn": "-sS",
        "udp": "-sU",
        "version": "-sV",
        "os": "-O",
    }
    flags = flags_map.get(scan_type, "-sT")
    args = [flags, "-Pn", "--open"]
    if ports:
        args += ["-p", ports]
    args += [host]
    return await _run_tool("nmap", "nmap", args, timeout=timeout)


# ---------------------------------------------------------------------------
# 漏洞扫描
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 nuclei 执行模板化漏洞扫描（基于已知 CVE 模板）。"
        "参数: target 目标 URL/域名/IP; severity 最低严重级别"
        "(info/low/medium/high/critical)，默认 info; tags 模板标签过滤(如 'cve,oast')。"
        "注意: 仅限已授权目标。"
    ),
    category="security",
    tags=["vuln", "scan", "nuclei"],
)
async def nuclei_scan(
    target: str,
    severity: str = "info",
    tags: Optional[str] = None,
    timeout: int = 180,
) -> str:
    """nuclei 模板化漏洞扫描。"""
    args = ["-u", target, "-severity", severity]
    if tags:
        args += ["-tags", tags]
    args += ["-silent"]
    return await _run_tool("nuclei", "nuclei", args, timeout=timeout)


@register_tool(
    description=(
        "使用 nikto 执行 Web 服务器漏洞扫描（发现过期组件、错误配置、敏感文件）。"
        "参数: url 目标 URL(如 https://example.com); 输出包含发现的风险项列表。"
    ),
    category="security",
    tags=["web", "vuln", "nikto"],
)
async def nikto_scan(url: str, timeout: int = 180) -> str:
    """nikto Web 服务器扫描。"""
    return await _run_tool("nikto", "nikto", ["-h", url], timeout=timeout)


# ---------------------------------------------------------------------------
# SQL 注入测试
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 sqlmap 检测 SQL 注入漏洞（仅限已授权目标）。"
        "参数: url 目标 URL; data POST 数据(可选); level 检测级别 1-5 默认 1;"
        "risk 风险等级 1-3 默认 1; 自动拦截 --os-shell 等武器化参数。"
        "输出包含检测到的注入点与数据库信息。"
    ),
    category="security",
    tags=["web", "sqli", "sqlmap"],
)
async def sqlmap_check(
    url: str,
    data: Optional[str] = None,
    level: int = 1,
    risk: int = 1,
    timeout: int = 300,
) -> str:
    """sqlmap SQL 注入检测（只读探测，无武器化）。"""
    args = ["-u", url, "--batch", "--level", str(level), "--risk", str(risk)]
    if data:
        args += ["--data", data]
    args += ["--smart"]
    return await _run_tool("sqlmap", "sqlmap", args, timeout=timeout)


# ---------------------------------------------------------------------------
# 目录爆破 / 枚举
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 gobuster 执行目录/子域名枚举（基于字典）。"
        "参数: url 目标 URL 或域名; wordlist 字典路径(默认常用字典);"
        "mode: dir(目录) 或 dns(子域名); 输出包含发现的路径/子域名。"
    ),
    category="security",
    tags=["enum", "gobuster", "web"],
)
async def gobuster_enum(
    url: str,
    mode: str = "dir",
    wordlist: str = "/usr/share/wordlists/dirb/common.txt",
    timeout: int = 300,
) -> str:
    """gobuster 目录 / 子域名枚举。"""
    args = []
    if mode == "dns":
        args = ["dns", "-d", url, "-w", wordlist]
    else:
        args = ["dir", "-u", url, "-w", wordlist]
    return await _run_tool("gobuster", "gobuster", args, timeout=timeout)


# ---------------------------------------------------------------------------
# 凭据爆破（审计场景）
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 hydra 执行在线服务弱口令审计（仅限已授权目标，如自建服务安全自检）。"
        "参数: target 目标(host); service 服务类型(ssh/ftp/http-get/rdp/mysql/postgres);"
        "user 用户名或逗号分隔列表; password 单个密码 或 wordlist 字典路径;"
        "输出显示验证成功的凭据。"
    ),
    category="security",
    tags=["brute", "audit", "hydra"],
)
async def hydra_audit(
    target: str,
    service: str = "ssh",
    user: str = "root",
    password: Optional[str] = None,
    wordlist: Optional[str] = None,
    timeout: int = 300,
) -> str:
    """hydra 在线服务弱口令审计。"""
    if password and wordlist:
        return "错误: password 与 wordlist 只能提供一个"
    if "," in user:
        args = ["-L", user]
    else:
        args = ["-l", user]
    if password:
        args += ["-p", password]
    elif wordlist:
        args += ["-P", wordlist]
    else:
        args += ["-P", "/usr/share/wordlists/rockyou.txt"]
    args += [target, service]
    return await _run_tool("hydra", "hydra", args, timeout=timeout)


# ---------------------------------------------------------------------------
# 流量分析
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 tshark(Wireshark CLI) 分析 pcap 流量包。"
        "参数: pcap_file pcap 文件路径; filter BPF/Wireshark 过滤表达式(可选);"
        "display 显示字段(默认基础列); limit 最多显示的包数(默认 50);"
        "输出为结构化包摘要（源/目的/协议/关键字段）。"
    ),
    category="security",
    tags=["traffic", "pcap", "tshark"],
)
async def tshark_analyze(
    pcap_file: str,
    filter: Optional[str] = None,
    display: Optional[str] = None,
    limit: int = 50,
    timeout: int = 120,
) -> str:
    """tshark 流量包分析。"""
    args = ["-r", pcap_file, "-Y", filter] if filter else ["-r", pcap_file]
    if display:
        args += ["-T", "fields", "-e", display]
    args += ["-c", str(limit)]
    return await _run_tool("tshark", "tshark", args, timeout=timeout)


# ---------------------------------------------------------------------------
# 哈希破解（授权取证场景）
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 hashcat 进行密码哈希离线破解（授权取证/自检场景）。"
        "参数: hash_file 哈希文件路径; mode hashcat 模式号(0=MD5,1000=NTLM,22000=WPA,"
        "默认 0); wordlist 字典路径; output_file 输出文件(可选); 输出显示破解结果。"
    ),
    category="security",
    tags=["crypto", "hashcat", "forensic"],
)
async def hashcat_crack(
    hash_file: str,
    mode: int = 0,
    wordlist: str = "/usr/share/wordlists/rockyou.txt",
    output_file: Optional[str] = None,
    timeout: int = 600,
) -> str:
    """hashcat 哈希破解（授权场景）。"""
    args = ["-m", str(mode), "-a", "0", hash_file, wordlist, "--force"]
    if output_file:
        args += ["-o", output_file]
    return await _run_tool("hashcat", "hashcat", args, timeout=timeout)


# ---------------------------------------------------------------------------
# Metasploit 框架（受控利用验证）
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 Metasploit (msfconsole) 在授权范围内验证漏洞可利用性。"
        "参数: resource_file 包含 msf 命令的资源脚本路径;"
        "输出显示模块执行结果。注意: 平台拦截直接内联的武器化命令，"
        "请使用资源脚本并通过 BAIZE_ALLOW_WEAPONIZED=1 显式授权。"
    ),
    category="security",
    tags=["exploit", "metasploit", "msf"],
)
async def metasploit_run(
    resource_file: str,
    timeout: int = 300,
) -> str:
    """msfconsole 资源脚本执行（受控验证）。"""
    args = ["-q", "-r", resource_file]
    return await _run_tool("metasploit", "msfconsole", args, timeout=timeout)


# ---------------------------------------------------------------------------
# 确保模块导入即注册
# ---------------------------------------------------------------------------

__all__ = [
    "nmap_scan",
    "nuclei_scan",
    "nikto_scan",
    "sqlmap_check",
    "gobuster_enum",
    "hydra_audit",
    "tshark_analyze",
    "hashcat_crack",
    "metasploit_run",
]
