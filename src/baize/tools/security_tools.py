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
import shutil
import tempfile
from typing import Optional

from baize.executors import ExecutorConfig, build_executor
from baize.tools.registry import register_tool

logger = logging.getLogger("baize.tools.security")


class ToolBinaryMissing(RuntimeError):
    """工具依赖的系统二进制未安装。

    抛出后由 agent 主循环的自动安装自洽修复（``auto_install``）识别并
    尝试安装 + 重试一次，避免返回裸 ``exit=127`` 报错让模型反复无效重试。
    """

    def __init__(self, binary: str) -> None:
        self.binary = binary
        super().__init__(f"command not found: {binary}")


def _is_binary_available(binary: str) -> bool:
    """预检命令是否存在于 PATH。

    只对纯命令名预检；含路径或 shell 元字符的输入不做预检（交由执行器
    按原样处理），避免误伤。
    """
    if not binary or "/" in binary or any(c in binary for c in " \t|&;<>$`"):
        return True
    return shutil.which(binary) is not None


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
    """构造命令并通过执行器运行（含危险参数校验 + 二进制缺失预检）。"""
    # 二进制缺失预检：直接抛出可识别的缺失异常，交由 agent 主循环的
    # auto_install 自洽修复（安装 + 重试一次）处理，避免返回裸 exit=127
    # 报错导致模型反复无效重试、白烧预算。
    if not _is_binary_available(binary):
        raise ToolBinaryMissing(binary)
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
        "对目标 Web 服务器执行轻量级安全配置扫描（纯 Python 实现，速度远快于外部 nikto，"
        "覆盖敏感文件暴露、安全响应头缺失、危险 HTTP 方法、服务版本信息泄露、"
        "Cookie 安全标志、目录列表等）。参数: url 目标 URL(如 https://example.com);"
        "输出以 + 开头的问题清单。"
    ),
    category="security",
    tags=["web", "vuln", "nikto", "misconfig"],
)
async def nikto_scan(url: str, timeout: int = 180) -> str:
    """轻量 Web 服务器安全配置扫描（纯 Python，替代慢速外部 nikto）。"""
    import asyncio as _asyncio
    import re as _re

    import httpx as _httpx

    target = str(url).strip()
    if not target:
        return "未提供目标 URL"
    if not target.startswith(("http://", "https://")):
        target = "http://" + target
    target = target.rstrip("/")

    findings: list[str] = []
    client_timeout = min(int(timeout) if timeout else 20, 20)
    async with _httpx.AsyncClient(
        timeout=client_timeout, follow_redirects=False, verify=False  # noqa: S501
    ) as client:
        try:
            base = await client.get(target + "/")
        except Exception as exc:  # noqa: BLE001
            return f"[nikto 请求失败] {type(exc).__name__}: {str(exc)[:120]}"

        server = base.headers.get("server", "")
        if server:
            findings.append(f"+ Server: {server}")
            if _re.search(r"[0-9]+\.[0-9]+", server):
                findings.append("+ 服务器精确版本号暴露，建议隐藏（ServerTokens/ServerSignature）")
        powered = base.headers.get("x-powered-by", "")
        if powered:
            findings.append(f"+ X-Powered-By: {powered}（技术栈信息泄露）")

        # 1) 安全响应头缺失
        sec_headers = {
            "x-content-type-options": "X-Content-Type-Options",
            "x-frame-options": "X-Frame-Options",
            "content-security-policy": "Content-Security-Policy",
            "referrer-policy": "Referrer-Policy",
            "permissions-policy": "Permissions-Policy",
        }
        if target.startswith("https://"):
            sec_headers["strict-transport-security"] = "Strict-Transport-Security"
        for key, label in sec_headers.items():
            if key not in base.headers:
                findings.append(f"+ 缺少安全响应头: {label}")

        # 2) Cookie 安全标志
        for sc in base.headers.get_list("set-cookie"):
            ck_name = sc.split("=", 1)[0].strip() or "(cookie)"
            low = sc.lower()
            missing = [f for f in ("httponly", "secure", "samesite") if f not in low]
            if missing:
                findings.append(f"+ Cookie {ck_name} 缺少安全标志: {', '.join(missing)}")

        # 3) 危险 HTTP 方法
        try:
            opt = await client.request("OPTIONS", target + "/")
            allow = opt.headers.get("allow", "")
            if allow:
                findings.append(f"+ 可用的 HTTP 方法(Allow): {allow}")
                for risky in ("PUT", "DELETE", "TRACE"):
                    if risky in allow.upper():
                        findings.append(f"+ 启用潜在危险方法: {risky}")
        except Exception:  # noqa: BLE001
            pass

        # 4) 目录列表
        if _re.search(r"<title>\s*Index of|Index of /", base.text or "", _re.I):
            findings.append("+ 目录列表(Directory Listing)开启")

        # 5) 敏感文件/路径探测（并发）
        paths = [
            "/.git/config", "/.env", "/web.config", "/config.php.bak",
            "/phpinfo.php", "/info.php", "/test.php",
            "/server-status", "/server-info",
            "/robots.txt", "/crossdomain.xml", "/sitemap.xml",
            "/backup.zip", "/backup.tar.gz", "/db.sql", "/dump.sql",
            "/admin/", "/administrator/", "/phpmyadmin/",
            "/readme.html", "/README.md", "/package.json", "/.DS_Store",
        ]

        async def _probe(path: str):
            try:
                pr = await client.get(target + path)
            except Exception:  # noqa: BLE001
                return None
            code = pr.status_code
            if code not in (200, 204, 301, 302, 401, 403):
                return None
            snippet = ""
            if code in (200, 204):
                snippet = _re.sub(r"\s+", " ", (pr.text or "")[:80]).strip()
            return (path, code, snippet)

        for item in await _asyncio.gather(*[_probe(p) for p in paths]):
            if not item:
                continue
            path, code, snippet = item
            sensitive = any(k in path for k in (
                ".git", ".env", "web.config", "backup", "dump", ".sql",
                "config.", ".DS_Store", "phpinfo",
            ))
            if code in (200, 204) and sensitive:
                findings.append(f"+ 敏感文件可访问(可能信息泄露): {path} ({code}) {snippet}")
            elif code in (200, 204):
                findings.append(f"+ 发现可访问路径: {path} ({code}) {snippet}")
            else:
                findings.append(f"+ 路径存在(需鉴权/重定向): {path} ({code})")

    if not findings:
        return f"[nikto] {target} 未发现明显安全配置问题（轻量扫描）"
    return (
        f"[nikto] {target} 轻量安全配置扫描（共 {len(findings)} 项）:\n"
        + "\n".join(findings)
    )


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

_COMMON_WORDLIST_PATHS = (
    "/usr/share/wordlists/dirb/common.txt",
    "/usr/share/dirb/wordlists/common.txt",
    "/usr/share/wordlists/dirbuster/directory-list-2.3-small.txt",
    "/usr/share/seclists/Discovery/Web-Content/common.txt",
    "/usr/share/seclists/Discovery/Web-Content/raft-small-words.txt",
    "/usr/share/wordlists/seclists/Discovery/Web-Content/common.txt",
)

# 外部字典缺失时的最小内置字典。它不是完整爆破集，只保证枚举不会因
# “文件不存在”直接失败；agent 仍应继续用 http_request 验证命中结果。
_BUILTIN_WORDLIST = (
    "admin", "administrator", "login", "signin", "logout", "register",
    "api", "api/v1", "portal", "home", "dashboard", "console", "manage",
    "user", "users", "account", "profile", "settings", "config", "backup",
    "static", "assets", "uploads", "download", "downloads", "files",
    "robots.txt", "sitemap.xml", ".well-known", ".well-known/security.txt",
    "health", "healthz", "status", "metrics", "debug", "test", "dev",
    "graphql", "swagger", "swagger.json", "openapi.json", "api-docs",
    "actuator", "actuator/health", "server-status", "phpinfo.php",
    "wp-admin", "wp-login.php", "xmlrpc.php", "git", ".git/config",
    ".env", "web.config", "crossdomain.xml", "favicon.ico",
)


def _resolve_wordlist(path: str) -> tuple[str, str]:
    """返回可用字典路径及提示。

    优先使用调用方指定且真实存在的路径；否则在常见字典位置中查找；
    都没有时生成一个最小内置字典，避免 gobuster 因 “wordlist does not exist”
    直接中止整个枚举方向。
    """
    requested = os.path.expanduser(str(path or "").strip())
    if requested and os.path.isfile(requested):
        return requested, ""

    for candidate in _COMMON_WORDLIST_PATHS:
        if os.path.isfile(candidate):
            return candidate, f"[gobuster] 指定字典不存在，已改用 {candidate}"

    fd, fallback = tempfile.mkstemp(prefix="baize-gobuster-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_BUILTIN_WORDLIST) + "\n")
    return fallback, (
        "[gobuster] 指定字典和常见字典均不存在，已使用内置最小字典；"
        "结果只覆盖常见路径，请结合 http_request 继续验证"
    )


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
    resolved_wordlist, note = _resolve_wordlist(wordlist)
    args = []
    if mode == "dns":
        args = ["dns", "-d", url, "-w", resolved_wordlist]
    else:
        args = ["dir", "-u", url, "-w", resolved_wordlist]
    result = await _run_tool("gobuster", "gobuster", args, timeout=timeout)
    return f"{note}\n{result}" if note else result


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
