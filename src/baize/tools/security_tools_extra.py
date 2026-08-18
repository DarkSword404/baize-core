"""Baize 扩展安全工具集 — 更多网络安全常用工具封装。

在 ``security_tools.py``（核心 9 个）基础上，覆盖:
- 信息收集 / 侦察（whois / dns / 子域名 / http 探测 / ssl 证书 / web 指纹 / waf 检测）
- 漏洞研究（searchsploit / cve 查询）
- 爆破与枚举（ffuf / arp 扫描 / masscan / traceroute）
- 无线安全（aircrack / airodump）
- 取证（exiftool / strings / binwalk / john / hashid）

所有工具复用 :mod:`baize.tools.security_tools` 的 `_run_tool` 辅助
（危险参数拦截 + 执行器抽象 + 结构化输出），并通过 ``register_tool`` 注册。

**授权声明**: 仅用于已获授权的渗透测试 / 安全评估 / 防御取证场景。
"""

from __future__ import annotations

from typing import Optional

from baize.tools.registry import register_tool
from baize.tools.security_tools import _run_tool


# ---------------------------------------------------------------------------
# 信息收集 / 侦察
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 whois 查询域名的注册信息（注册人、注册商、DNS 服务器、创建/到期时间）。"
        "参数: domain 要查询的域名或 IP; 输出原始 whois 记录摘要。"
    ),
    category="security",
    tags=["recon", "whois", "osint"],
)
async def whois_lookup(domain: str, timeout: int = 60) -> str:
    """whois 域名注册信息查询。"""
    return await _run_tool("whois", "whois", [domain], timeout=timeout)


@register_tool(
    description=(
        "使用 dig 执行 DNS 枚举查询。参数: domain 目标域名; record_type 记录类型"
        "(A/AAAA/MX/NS/TXT/SOA/CNAME，默认 A); server DNS 服务器(可选，如 8.8.8.8);"
        "输出对应 DNS 记录。"
    ),
    category="security",
    tags=["recon", "dns", "dig"],
)
async def dns_lookup(
    domain: str,
    record_type: str = "A",
    server: Optional[str] = None,
    timeout: int = 60,
) -> str:
    """dig DNS 记录枚举。"""
    if server:
        args = [f"@{server}", "+short", domain, record_type]
    else:
        args = ["+short", domain, record_type]
    return await _run_tool("dig", "dig", args, timeout=timeout)


@register_tool(
    description=(
        "使用 crt.sh 证书透明度日志查询域名关联的子域名（无需安装工具，走 HTTPS API）。"
        "参数: domain 目标域名(如 example.com); limit 返回子域名数量上限(默认 50);"
        "输出发现的相关子域名列表。"
    ),
    category="security",
    tags=["recon", "subdomain", "osint", "crt.sh"],
)
async def crt_sh_lookup(domain: str, limit: int = 50, timeout: int = 60) -> str:
    """crt.sh 证书透明度子域名枚举（纯 Python HTTP，无需外部二进制）。"""
    import json
    import urllib.parse
    import urllib.request

    url = f"https://crt.sh/?q=%25.{urllib.parse.quote(domain)}&output=json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return f"[crt.sh 查询失败] {exc}"
    subs: set[str] = set()
    for row in data:
        name = row.get("name_value", "")
        for part in name.split("\n"):
            part = part.strip().lower()
            if part and part.endswith(domain.lower()) and part not in subs:
                subs.add(part)
    if not subs:
        return "未在证书透明度日志中发现相关子域名"
    total = len(subs)
    listed = "\n".join(sorted(subs)[:limit])
    return f"发现 {total} 个相关子域名(显示前 {min(limit, total)}):\n{listed}"


@register_tool(
    description=(
        "使用 httpx 探测目标 HTTP/HTTPS 服务的存活状态、标题、服务器指纹与技术栈。"
        "参数: target 目标 URL 或域名(可逗号分隔多个); follow_redirects 是否跟随重定向"
        "(默认否); 输出每台主机的状态码/标题/服务器头/技术栈。"
    ),
    category="security",
    tags=["recon", "http", "httpx", "fingerprint"],
)
async def http_probe(
    target: str,
    follow_redirects: bool = False,
    timeout: int = 120,
) -> str:
    """httpx Web 服务存活探测与指纹。"""
    args = ["-silent", "-title", "-web-server", "-tech-detect", "-status-code"]
    if follow_redirects:
        args.append("-follow-redirects")
    args.append(target)
    return await _run_tool("httpx", "httpx", args, timeout=timeout)


@register_tool(
    description=(
        "使用 openssl 获取目标 TLS/SSL 证书信息（签发者、主体、有效期、SAN）。"
        "参数: host 目标主机; port 端口(默认 443); 输出证书详情摘要。"
    ),
    category="security",
    tags=["recon", "ssl", "tls", "openssl"],
)
async def ssl_cert_info(host: str, port: int = 443, timeout: int = 60) -> str:
    """openssl s_client 获取 TLS 证书信息。"""
    args = [
        "s_client",
        "-connect",
        f"{host}:{port}",
        "-servername",
        host,
        "-showcerts",
        "< /dev/null",
    ]
    return await _run_tool("openssl", "openssl", args, timeout=timeout)


@register_tool(
    description=(
        "使用 whatweb 对目标 Web 应用进行指纹识别（CMS、框架、服务器、JS 库、版本）。"
        "参数: url 目标 URL(如 https://example.com); 输出识别的技术栈与版本。"
    ),
    category="security",
    tags=["recon", "fingerprint", "whatweb", "web"],
)
async def whatweb_fingerprint(url: str, timeout: int = 120) -> str:
    """whatweb Web 应用指纹识别。"""
    return await _run_tool("whatweb", "whatweb", ["-v", url], timeout=timeout)


@register_tool(
    description=(
        "使用 wafw00f 检测目标是否位于 WAF(Web 应用防火墙) 之后，并识别 WAF 类型。"
        "参数: url 目标 URL; 输出检测到的 WAF 厂商/产品（若有）。"
    ),
    category="security",
    tags=["recon", "waf", "wafw00f", "web"],
)
async def waf_detect(url: str, timeout: int = 120) -> str:
    """wafw00f WAF 检测。"""
    return await _run_tool("wafw00f", "wafw00f", [url], timeout=timeout)


# ---------------------------------------------------------------------------
# 漏洞研究
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 searchsploit 在本地 Exploit-DB 中检索漏洞利用（PoC/exp 代码）。"
        "参数: query 搜索关键词(如 'apache 2.4.49' 或 CVE 编号);"
        "输出匹配的漏洞利用条目及路径。"
    ),
    category="security",
    tags=["vuln", "exploit", "searchsploit"],
)
async def searchsploit_lookup(query: str, timeout: int = 60) -> str:
    """searchsploit 本地漏洞利用检索。"""
    return await _run_tool("searchsploit", "searchsploit", [query], timeout=timeout)


@register_tool(
    description=(
        "查询 NVD(National Vulnerability Database) CVE 漏洞详情（纯 Python HTTP）。"
        "参数: cve_id CVE 编号(如 CVE-2021-44228); 输出漏洞描述、CVSS 评分与参考链接。"
    ),
    category="security",
    tags=["vuln", "cve", "nvd", "osint"],
)
async def cve_lookup(cve_id: str, timeout: int = 60) -> str:
    """NVD CVE 漏洞详情查询（无需外部二进制）。"""
    import json
    import urllib.request

    cve_id = cve_id.strip().upper()
    if not cve_id.startswith("CVE-"):
        return "错误: 请输入合法 CVE 编号，如 CVE-2021-44228"
    url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?cveId={cve_id}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return f"[NVD 查询失败] {exc}"
    vulns = data.get("vulnerabilities", [])
    if not vulns:
        return f"未在 NVD 中找到 {cve_id}"
    vuln = vulns[0].get("cve", {})
    desc = ""
    for d in vuln.get("descriptions", []):
        if d.get("lang") == "en":
            desc = d.get("value", "")
            break
    metrics = vuln.get("metrics", {})
    cvss = ""
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        if key in metrics:
            m = metrics[key][0].get("cvssData", {})
            cvss = f"CVSS {m.get('baseScore', '?')} ({m.get('baseSeverity', 'N/A')}) "
            break
    refs = [r.get("url", "") for r in vuln.get("references", [])][:3]
    return f"{cve_id} {cvss}\n描述: {desc or 'N/A'}\n参考:\n" + "\n".join(refs or ["无"])


# ---------------------------------------------------------------------------
# 爆破与枚举
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 ffuf 执行 Web 目录/文件模糊测试（基于字典）。"
        "参数: url 目标 URL，用 FUZZ 占位(如 https://example.com/FUZZ);"
        "wordlist 字典路径(默认 common.txt); filter_status 过滤状态码(可选,逗号分隔);"
        "输出发现的路径与状态码。"
    ),
    category="security",
    tags=["enum", "ffuf", "web", "fuzz"],
)
async def ffuf_fuzz(
    url: str,
    wordlist: str = "/usr/share/wordlists/dirb/common.txt",
    filter_status: Optional[str] = None,
    timeout: int = 300,
) -> str:
    """ffuf Web 目录模糊测试。"""
    args = ["-u", url, "-w", wordlist, "-mc", "200,204,301,302,307,308,401,403"]
    if filter_status:
        args += ["-fc", filter_status]
    return await _run_tool("ffuf", "ffuf", args, timeout=timeout)


@register_tool(
    description=(
        "使用 arp-scan 在本地网段执行 ARP 主机发现（二层存活探测）。"
        "参数: network 目标网段(如 192.168.1.0/24); 输出存活主机 IP 与 MAC。"
    ),
    category="security",
    tags=["network", "arp", "discovery", "l2"],
)
async def arp_scan(network: str, timeout: int = 120) -> str:
    """arp-scan 局域网主机发现。"""
    return await _run_tool("arp-scan", "arp-scan", [network], timeout=timeout)


@register_tool(
    description=(
        "使用 masscan 执行大规模端口扫描（高速扫描，用于侦察阶段快速定位开放端口）。"
        "参数: target 目标(IP/网段，可逗号分隔); ports 端口范围(默认 1-1000);"
        "rate 每秒发包速率(默认 1000); 输出发现的开放端口。"
    ),
    category="security",
    tags=["network", "scan", "masscan"],
)
async def masscan_scan(
    target: str,
    ports: str = "1-1000",
    rate: int = 1000,
    timeout: int = 300,
) -> str:
    """masscan 大规模端口扫描。"""
    args = [target, "-p", ports, "--rate", str(rate)]
    return await _run_tool("masscan", "masscan", args, timeout=timeout)


@register_tool(
    description=(
        "使用 traceroute 分析到目标主机的路由路径（网络拓扑侦察）。"
        "参数: host 目标主机/域名; max_hops 最大跳数(默认 30);"
        "输出经过的每一跳 IP 与延迟。"
    ),
    category="security",
    tags=["network", "traceroute", "recon"],
)
async def traceroute_analyze(host: str, max_hops: int = 30, timeout: int = 120) -> str:
    """traceroute 路由路径分析。"""
    args = ["-m", str(max_hops), host]
    return await _run_tool("traceroute", "traceroute", args, timeout=timeout)


# ---------------------------------------------------------------------------
# 无线安全
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 airodump-ng 扫描附近无线网络（SSID、BSSID、信道、加密方式）。"
        "参数: interface 无线网卡接口名(如 wlan0mon); duration 扫描时长秒数(默认 30);"
        "输出发现的无线网络列表。注意: 需 root 权限与监听模式网卡。"
    ),
    category="security",
    tags=["wifi", "airodump", "wireless"],
)
async def airodump_scan(interface: str, duration: int = 30, timeout: int = 120) -> str:
    """airodump-ng 无线网络扫描。

    duration 控制实际扫描时长：airodump-ng 需以超时终止（无法自行退出），
    因此将执行超时设为 duration+15 秒（默认额外缓冲），timeout 为硬上限。
    """
    args = [interface, "--band", "abg", "--write", "/tmp/baize_wifi", "--write-format", "csv"]
    effective_timeout = max(timeout, duration + 15)
    return await _run_tool("airodump-ng", "airodump-ng", args, timeout=effective_timeout)


@register_tool(
    description=(
        "使用 aircrack-ng 破解 WPA/WPA2 握手包中的预共享密钥（授权审计场景）。"
        "参数: capture_file 包含握手包的 .cap 文件; wordlist 字典路径;"
        "输出破解出的 PSK(若有)。"
    ),
    category="security",
    tags=["wifi", "aircrack", "wireless", "crack"],
)
async def aircrack_crack(
    capture_file: str,
    wordlist: str = "/usr/share/wordlists/rockyou.txt",
    timeout: int = 600,
) -> str:
    """aircrack-ng WPA 握手破解（授权场景）。"""
    args = ["-w", wordlist, capture_file]
    return await _run_tool("aircrack-ng", "aircrack-ng", args, timeout=timeout)


# ---------------------------------------------------------------------------
# 取证
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "使用 exiftool 提取文件元数据（EXIF、GPS、作者、软件、时间戳等）。"
        "参数: file_path 目标文件路径; 输出全部可提取的元数据字段。"
    ),
    category="security",
    tags=["forensic", "metadata", "exiftool"],
)
async def exiftool_meta(file_path: str, timeout: int = 60) -> str:
    """exiftool 文件元数据提取。"""
    return await _run_tool("exiftool", "exiftool", [file_path], timeout=timeout)


@register_tool(
    description=(
        "使用 strings 提取二进制/内存文件中的可打印字符串（线索挖掘）。"
        "参数: file_path 目标文件路径; min_len 最短字符串长度(默认 6);"
        "输出提取的可疑字符串（截断显示）。"
    ),
    category="security",
    tags=["forensic", "strings", "reverse"],
)
async def strings_analyze(file_path: str, min_len: int = 6, timeout: int = 60) -> str:
    """strings 二进制字符串提取。"""
    args = ["-n", str(min_len), file_path]
    return await _run_tool("strings", "strings", args, timeout=timeout)


@register_tool(
    description=(
        "使用 binwalk 分析固件/镜像文件，识别嵌入的文件系统与压缩数据。"
        "参数: file_path 目标固件文件路径; extract 是否尝试提取嵌入内容(默认否);"
        "输出扫描发现的签名与偏移量。"
    ),
    category="security",
    tags=["forensic", "firmware", "binwalk"],
)
async def binwalk_analyze(
    file_path: str,
    extract: bool = False,
    timeout: int = 300,
) -> str:
    """binwalk 固件分析。"""
    args = ["-e"] if extract else []
    args.append(file_path)
    return await _run_tool("binwalk", "binwalk", args, timeout=timeout)


@register_tool(
    description=(
        "使用 John the Ripper 破解密码哈希文件（授权取证场景）。"
        "参数: hash_file 哈希文件路径; format 哈希格式(可选，如 raw-md5/nt);"
        "wordlist 字典路径(默认 rockyou.txt); 输出破解出的密码。"
    ),
    category="security",
    tags=["forensic", "crack", "john", "password"],
)
async def john_crack(
    hash_file: str,
    format: Optional[str] = None,
    wordlist: str = "/usr/share/wordlists/rockyou.txt",
    timeout: int = 600,
) -> str:
    """John the Ripper 哈希破解（授权场景）。"""
    args = ["--wordlist=" + wordlist]
    if format:
        args.append("--format=" + format)
    args.append(hash_file)
    return await _run_tool("john", "john", args, timeout=timeout)


@register_tool(
    description=(
        "使用 hashid 识别密码哈希的类型（MD5/SHA/NTLM/bcrypt 等）。"
        "参数: hash_value 要识别的哈希字符串; 输出候选哈希类型列表。"
    ),
    category="security",
    tags=["forensic", "hashid", "password", "identify"],
)
async def hashid_identify(hash_value: str, timeout: int = 30) -> str:
    """hashid 哈希类型识别。"""
    return await _run_tool("hashid", "hashid", ["-m", hash_value], timeout=timeout)


# ---------------------------------------------------------------------------
# 确保模块导入即注册
# ---------------------------------------------------------------------------

__all__ = [
    "whois_lookup",
    "dns_lookup",
    "crt_sh_lookup",
    "http_probe",
    "ssl_cert_info",
    "whatweb_fingerprint",
    "waf_detect",
    "searchsploit_lookup",
    "cve_lookup",
    "ffuf_fuzz",
    "arp_scan",
    "masscan_scan",
    "traceroute_analyze",
    "airodump_scan",
    "aircrack_crack",
    "exiftool_meta",
    "strings_analyze",
    "binwalk_analyze",
    "john_crack",
    "hashid_identify",
]
