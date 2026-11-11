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

import html
import re
from typing import Optional
from urllib.parse import urljoin

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
    timeout: int = 30,
) -> str:
    """Web 服务存活探测与指纹（Python httpx 实现，不依赖外部 httpx CLI）。

    target 支持逗号分隔多个 URL；输出每台主机的状态码/标题/Server/技术栈。
    """
    import re as _re

    import httpx as _httpx

    targets = [t.strip() for t in str(target).replace("\n", ",").split(",") if t.strip()]
    if not targets:
        return "未提供有效目标"

    # 不带 scheme 的目标默认走 https（失败再由调用方自行尝试 http）
    norm_targets = []
    for t in targets:
        if not t.startswith(("http://", "https://")):
            t = "https://" + t
        norm_targets.append(t)

    # 简易技术栈识别：响应头 + cookie 名 + HTML 特征
    def _detect_tech(resp: _httpx.Response, body: str) -> list[str]:
        tech: list[str] = []
        header_blob = "\n".join(f"{k}: {v}" for k, v in resp.headers.items()).lower()
        cookies = "; ".join(resp.cookies.keys()).lower()
        checks = [
            ("nginx", "nginx"), ("apache", "apache"), ("iis", "microsoft-iis"),
            ("tomcat", "tomcat"), ("jboss", "jboss"), ("weblogic", "weblogic"),
            ("php", "php"), ("asp.net", "asp.net"), ("jsp", "jsp"),
            ("shiro", "rememberme"), ("spring", "spring"),
            ("vue", "vue"), ("react", "react"), ("angular", "angular"),
            ("jquery", "jquery"), ("bootstrap", "bootstrap"),
            ("jenkins", "jenkins"), ("grafana", "grafana"),
        ]
        haystack = header_blob + "\n" + cookies + "\n" + body[:5000].lower()
        for name, sig in checks:
            if sig in haystack and name not in tech:
                tech.append(name)
        return tech

    lines: list[str] = []
    async with _httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=follow_redirects,
        verify=False,  # 渗透目标常用自签名证书
    ) as client:
        for url in norm_targets:
            try:
                resp = await client.get(url)
                body = resp.text[:20000]
                m = _re.search(r"<title[^>]*>(.*?)</title>", body, _re.I | _re.S)
                title = _re.sub(r"\s+", " ", m.group(1)).strip()[:80] if m else ""
                server = resp.headers.get("server", "")
                powered = resp.headers.get("x-powered-by", "")
                tech = ",".join(_detect_tech(resp, body))
                lines.append(
                    f"[{resp.status_code}] {url}"
                    + (f" | {title}" if title else "")
                    + (f" | Server: {server}" if server else "")
                    + (f" | X-Powered-By: {powered}" if powered else "")
                    + (f" | Tech: {tech}" if tech else "")
                )
            except _httpx.InvalidURL:
                lines.append(f"[ERR] {url} | 无效 URL")
            except Exception as exc:  # noqa: BLE001
                # https 失败时自动回退 http 再试一次
                if url.startswith("https://"):
                    http_url = "http://" + url[len("https://"):]
                    try:
                        resp = await client.get(http_url)
                        body = resp.text[:20000]
                        m = _re.search(r"<title[^>]*>(.*?)</title>", body, _re.I | _re.S)
                        title = _re.sub(r"\s+", " ", m.group(1)).strip()[:80] if m else ""
                        server = resp.headers.get("server", "")
                        tech = ",".join(_detect_tech(resp, body))
                        lines.append(
                            f"[{resp.status_code}] {http_url} (http回退)"
                            + (f" | {title}" if title else "")
                            + (f" | Server: {server}" if server else "")
                            + (f" | Tech: {tech}" if tech else "")
                        )
                        continue
                    except Exception as exc2:  # noqa: BLE001
                        lines.append(f"[ERR] {url} | {type(exc2).__name__}: {str(exc2)[:100]}")
                        continue
                lines.append(f"[ERR] {url} | {type(exc).__name__}: {str(exc)[:100]}")
    return "\n".join(lines)


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


# Web 指纹签名库：(插件名, 匹配源, 正则, 版本捕获组索引或 None)
# 匹配源: "server"=Server 头, "x-powered-by"=X-Powered-By 头, "html"=响应正文
_WEB_FINGERPRINTS: list[tuple[str, str, str, int | None]] = [
    ("IIS", "server", r"microsoft-iis(?:/([0-9.]+))?", 1),
    ("Nginx", "server", r"nginx(?:/([0-9.]+))?", 1),
    ("Apache", "server", r"apache(?:/([0-9.]+))?", 1),
    ("Tomcat", "server", r"(?:coyote|tomcat)(?:/([0-9.]+))?", 1),
    ("Jetty", "server", r"jetty(?:\(([0-9.]+))?", 1),
    ("OpenResty", "server", r"openresty(?:/([0-9.]+))?", 1),
    ("LiteSpeed", "server", r"litespeed", None),
    ("ASP.NET", "x-powered-by", r"asp\.net", None),
    ("PHP", "x-powered-by", r"php(?:/([0-9.]+))?", 1),
    ("Express", "x-powered-by", r"express", None),
    ("WordPress", "html",
     r'name=["\']generator["\'][^>]+content=["\']WordPress ?([0-9.]+)', 1),
    ("WordPress", "html", r"/wp-(?:content|includes|login)", None),
    ("Joomla", "html",
     r'name=["\']generator["\'][^>]+content=["\']Joomla!? ?-? ?([0-9.]+)', 1),
    ("Joomla", "html", r"/components/com_|/media/jui/", None),
    ("Drupal", "html",
     r'name=["\']Generator["\'][^>]+content=["\']Drupal ?([0-9.]+)', 1),
    ("Drupal", "html", r"Drupal\.settings|/sites/default/files", None),
    ("jQuery", "html", r"jquery[-.]?([0-9]+\.[0-9]+\.[0-9]+)?(?:\.min)?\.js", 1),
    ("Bootstrap", "html",
     r"bootstrap[-.]?([0-9]+\.[0-9]+\.[0-9]+)?(?:\.min)?\.(?:css|js)", 1),
    ("Vue.js", "html", r"vue[-.]?([0-9]+\.[0-9]+\.[0-9]+)?(?:\.min)?\.js", 1),
    ("React", "html", r"react[-.]?([0-9]+\.[0-9]+\.[0-9]+)?(?:\.min)?\.js", 1),
    ("Angular", "html", r"angular[-.]?([0-9]+\.[0-9]+\.[0-9]+)?(?:\.min)?\.js", 1),
    ("phpMyAdmin", "html", r"phpmyadmin", None),
    ("Google Analytics", "html",
     r"(?:google-analytics\.com|googletagmanager\.com)", None),
    ("Cloudflare", "header", r"cloudflare", None),
]


@register_tool(
    description=(
        "对目标 Web 应用进行指纹识别（CMS、框架、服务器、JS 库及版本）。"
        "纯 Python 实现，不依赖外部 whatweb，速度快且结果确定。"
        "参数: url 目标 URL(如 https://example.com); 输出识别的技术栈与版本。"
    ),
    category="security",
    tags=["recon", "fingerprint", "whatweb", "web"],
)
async def whatweb_fingerprint(url: str, timeout: int = 120) -> str:
    """Web 指纹识别（纯 Python，替代外部 whatweb）。"""
    import re as _re

    import httpx as _httpx

    target = str(url).strip()
    if not target:
        return "未提供目标 URL"
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    async def _fetch(u: str):
        async with _httpx.AsyncClient(
            timeout=min(int(timeout) if timeout else 20, 20),
            follow_redirects=True,
            verify=False,  # noqa: S501
        ) as client:
            return await client.get(u)

    try:
        resp = await _fetch(target)
    except Exception as exc:  # noqa: BLE001
        # https 失败回退 http
        if target.startswith("https://"):
            fallback = "http://" + target[len("https://"):]
            try:
                resp = await _fetch(fallback)
                target = fallback
            except Exception as exc2:  # noqa: BLE001
                return f"[whatweb 请求失败] {type(exc2).__name__}: {str(exc2)[:120]}"
        else:
            return f"[whatweb 请求失败] {type(exc).__name__}: {str(exc)[:120]}"

    body = resp.text[:50000]
    server = resp.headers.get("server", "")
    powered = resp.headers.get("x-powered-by", "")
    header_blob = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())

    def _haystack(src: str) -> str:
        if src == "html":
            return body
        if src == "server":
            return server
        if src == "x-powered-by":
            return powered
        return header_blob

    plugins: list[str] = []
    for name, src, pattern, vgrp in _WEB_FINGERPRINTS:
        m = _re.search(pattern, _haystack(src), _re.I)
        if not m:
            continue
        ver = ""
        if vgrp is not None:
            try:
                ver = m.group(vgrp) or ""
            except (IndexError, _re.error):
                ver = ""
        label = f"{name}[{ver}]" if ver else name
        if label not in plugins:
            plugins.append(label)

    m_title = _re.search(r"<title[^>]*>(.*?)</title>", body, _re.I | _re.S)
    title = _re.sub(r"\s+", " ", m_title.group(1)).strip()[:80] if m_title else ""

    parts = [f"{target} [{resp.status_code}]"]
    if title:
        parts.append(f"Title[{title}]")
    if plugins:
        parts.append(", ".join(plugins))
    elif not title:
        parts.append("未识别到已知指纹")
    return " | ".join(parts)


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
# Web 证据提取（纯 Python，无外部命令）
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "从 HTML/JavaScript 文本中提取 Web 攻击面证据：script src、link href、"
        "form action/method/input、同源/绝对 URL、/api/ 路径、常见端点。"
        "参数: content HTML/JS 原文; base_url 可选，用于把相对路径转成绝对 URL。"
        "只做解析，不发请求，不判断漏洞。适合在 http_request 拿到页面后调用。"
    ),
    category="security",
    tags=["web", "recon", "html", "javascript", "artifacts"],
)
def extract_web_artifacts(content: str, base_url: str = "") -> str:
    """从 HTML/JS 文本提取表单、脚本、链接和 API 路径。"""
    text = str(content or "")
    if not text.strip():
        return "(空内容，未提取到任何 Web 证据)"

    def _attr_value(tag: str, name: str) -> list[str]:
        values: list[str] = []
        for m in re.finditer(
            rf"\b{name}\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
            tag,
            flags=re.IGNORECASE,
        ):
            value = next((v for v in m.groups() if v is not None), "")
            value = html.unescape(value).strip()
            if value:
                values.append(value)
        return values

    def _absolute(value: str) -> str:
        if not value:
            return value
        if base_url and value.startswith("/"):
            return urljoin(base_url, value)
        return value

    scripts: list[str] = []
    links: list[str] = []
    forms: list[str] = []
    for tag in re.findall(r"<script\b[^>]*>", text, flags=re.IGNORECASE):
        scripts.extend(_attr_value(tag, "src"))
    for tag in re.findall(r"<link\b[^>]*>", text, flags=re.IGNORECASE):
        links.extend(_attr_value(tag, "href"))
    for form_match in re.finditer(
        r"<form\b([^>]*)>(.*?)</form>", text, flags=re.IGNORECASE | re.DOTALL
    ):
        attrs, body = form_match.group(1), form_match.group(2)
        action = (_attr_value(attrs, "action") or [""])[0]
        method = ((_attr_value(attrs, "method") or ["GET"])[0] or "GET").upper()
        fields: list[str] = []
        for inp in re.findall(r"<input\b[^>]*>", body, flags=re.IGNORECASE):
            name = (_attr_value(inp, "name") or [""])[0]
            typ = (_attr_value(inp, "type") or ["text"])[0]
            if name:
                fields.append(f"{name}:{typ}")
        forms.append(f"{method} {_absolute(action) or '(no action)'} fields=[{', '.join(fields)}]")

    api_paths = sorted({
        html.unescape(p)
        for p in re.findall(r"""(?<![\w])/(?:api|rest|graphql|v\d+)[A-Za-z0-9_?&=./:{}\-]*""", text)
    })
    urls = sorted({
        html.unescape(u)
        for u in re.findall(r"https?://[^\s\"'<>]+", text)
    })

    parts: list[str] = []
    if scripts:
        parts.append("## Scripts\n" + "\n".join(f"- {_absolute(s)}" for s in dict.fromkeys(scripts)))
    if links:
        parts.append("## Links\n" + "\n".join(f"- {_absolute(s)}" for s in dict.fromkeys(links)))
    if forms:
        parts.append("## Forms\n" + "\n".join(f"- {f}" for f in dict.fromkeys(forms)))
    if api_paths:
        parts.append("## API paths\n" + "\n".join(f"- {p}" for p in api_paths))
    if urls:
        parts.append("## Absolute URLs\n" + "\n".join(f"- {u}" for u in urls[:30]))
    if not parts:
        return "(未提取到 script/form/link/API 路径；可能需要先拿到完整 HTML 或 JS 内容)"
    return "\n\n".join(parts)[:6000]


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
    "extract_web_artifacts",
]
