"""Baize 命令行入口。

子命令:
  baize serve   启动 Web API 服务（默认）
  baize doctor  环境自检：系统工具/模型/向量配置/浏览器依赖
  baize --version  查看版本
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# 白泽 50+ 工具依赖的系统二进制清单（与 install-tools.sh 保持一致）
SYSTEM_TOOLS: dict[str, str] = {
    # core
    "curl": "基础网络请求", "wget": "基础下载", "git": "代码/字典", "jq": "JSON 解析",
    "unzip": "解压", "openssl": "TLS/证书", "ssh": "SSH 连接", "nc": "端口测试 (netcat)",
    "dig": "DNS 查询", "whois": "域名信息", "traceroute": "路由追踪", "bwrap": "沙箱隔离 (bubblewrap)",
    # recon / web / password / forensic / wireless
    "nmap": "端口/服务扫描", "masscan": "大规模端口扫描", "arp-scan": "内网发现", "tshark": "流量分析",
    "sqlmap": "SQL 注入检测", "nikto": "Web 漏洞扫描", "hydra": "在线口令爆破",
    "john": "离线哈希破解", "hashcat": "GPU 哈希破解", "hashid": "哈希类型识别",
    "strings": "二进制字符串", "exiftool": "元数据提取", "binwalk": "固件分析",
    "aircrack-ng": "无线安全", "nuclei": "模板化漏洞扫描", "httpx": "HTTP 探测",
    "gobuster": "目录/子域爆破", "ffuf": "模糊测试", "wafw00f": "WAF 识别",
    "msfconsole": "Metasploit 框架", "searchsploit": "exploitdb 查询",
}

# LLM 供应商模型配置（OpenAI 协议兼容端点）
MODEL_CONFIG_PATHS = [
    Path(os.environ.get("BAIZE_DATA_DIR", "")).expanduser() / "model.json",
    Path.home() / ".baize" / "model.json",
]

C_RESET = "\033[0m"; C_GREEN = "\033[0;32m"; C_YELLOW = "\033[1;33m"; C_RED = "\033[0;31m"; C_CYAN = "\033[0;36m"


def _ok(msg: str) -> str:
    return f"  {C_GREEN}✓{C_RESET} {msg}"


def _warn(msg: str) -> str:
    return f"  {C_YELLOW}⚠{C_RESET} {msg}"


def _fail(msg: str) -> str:
    return f"  {C_RED}✗{C_RESET} {msg}"


def _print_heading(title: str) -> None:
    print(f"\n{C_CYAN}== {title} =={C_RESET}")


def check_system_tools() -> list[str]:
    """检查系统工具二进制是否就位，返回缺失清单。"""
    _print_heading("系统工具")
    missing: list[str] = []
    for bin_name, desc in SYSTEM_TOOLS.items():
        found = shutil.which(bin_name)
        if found:
            print(_ok(f"{bin_name:<14} {desc} ({found})"))
        else:
            print(_fail(f"{bin_name:<14} {desc} — 未安装"))
            missing.append(bin_name)
    if missing:
        print(_warn(f"缺失 {len(missing)} 个工具: {', '.join(missing)}"))
        print(_warn("运行 ./install-tools.sh --yes 一键预装（最小化 Ubuntu/Debian 上建议）"))
    else:
        print(_ok("全部系统工具已就绪"))
    return missing


def check_runtime_env() -> None:
    """检查 Python / Node 运行时与核心依赖。"""
    _print_heading("运行时环境")
    py = sys.version_info
    status = _ok if (py.major, py.minor) >= (3, 11) else _warn
    print(status(f"Python {py.major}.{py.minor}.{py.micro} (要求 3.11+)"))
    for mod in ("uvicorn", "fastapi", "openai", "httpx", "playwright"):
        try:
            __import__(mod)
            print(_ok(f"Python 包 {mod} 已安装"))
        except ImportError:
            print(_fail(f"Python 包 {mod} 未安装 — 请重新执行 ./setup.sh 或 pip install -e ."))
    node = shutil.which("node")
    if node:
        print(_ok(f"Node.js {os.popen(f'{node} --version').read().strip()}"))
    else:
        print(_fail("Node.js 未安装 — 前端构建需要 Node 18+"))

    # 前端构建产物
    web_dir = Path(__file__).resolve().parent.parent.parent / "web"
    dist = web_dir / "dist"
    if dist.is_dir():
        print(_ok(f"前端构建产物存在: {dist}"))
    else:
        print(_warn(f"前端未构建: {dist} — 请执行 cd web && npm install && npm run build"))


def check_model_config() -> None:
    """检查 LLM 模型配置。"""
    _print_heading("LLM 模型配置")
    found_cfg = False
    for p in MODEL_CONFIG_PATHS:
        if p and p.is_file():
            try:
                cfg = json.loads(p.read_text())
                model = cfg.get("model") or cfg.get("models", [{}])[0].get("model", "")
                base_url = cfg.get("base_url") or cfg.get("models", [{}])[0].get("base_url", "")
                print(_ok(f"模型配置已存在: {p}"))
                print(f"     model: {model}")
                print(f"     base_url: {base_url}")
                found_cfg = True
                break
            except Exception as exc:  # noqa: BLE001
                print(_fail(f"模型配置解析失败 ({p}): {exc}"))
    if not found_cfg:
        print(_fail("未找到模型配置 — 请在 Web「设置」页配置，或写入 ~/.baize/model.json"))
        print(_warn("配置项: base_url / api_key / model（OpenAI 协议兼容端点）"))


def check_browser() -> None:
    """检查 playwright 浏览器二进制。"""
    _print_heading("浏览器依赖")
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        print(_ok("playwright Python 包已安装"))
    except ImportError:
        print(_fail("playwright 未安装 — 运行: python3 -m pip install playwright && python3 -m playwright install chromium --with-deps"))
        return
    chromium = (
        Path.home() / ".cache" / "ms-playwright"
        if os.name != "nt"
        else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    )
    if chromium.is_dir() and any(chromium.iterdir()):
        print(_ok(f"Chromium 已下载: {chromium}"))
    else:
        print(_fail("Chromium 未下载 — 运行: python3 -m playwright install chromium --with-deps"))


def cmd_doctor(argv: list[str]) -> int:
    """baize doctor：环境自检。"""
    del argv
    print(f"{C_CYAN}═══════════════════════════════════════════{C_RESET}")
    print(f"{C_CYAN}  Baize 环境自检 (doctor){C_RESET}")
    print(f"{C_CYAN}═══════════════════════════════════════════{C_RESET}")
    missing = check_system_tools()
    check_runtime_env()
    check_model_config()
    check_browser()
    print()
    if missing:
        print(f"{C_YELLOW}结论: 有 {len(missing)} 个系统工具缺失，建议执行 ./install-tools.sh --yes 后重跑本命令。{C_RESET}")
    else:
        print(f"{C_GREEN}结论: 系统工具齐全；若其他项有 ✗/⚠，请按提示处理。{C_RESET}")
    return 0 if not missing else 1


def cmd_serve(argv: list[str]) -> int:
    """启动 Baize 后端服务。"""
    import uvicorn

    from baize.config import get_server_config

    cfg = get_server_config()
    uvicorn.run(
        "baize.api.app:create_baize_api_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
        **({"workers": int(os.environ.get("BAIZE_WORKERS", "1"))} if os.environ.get("BAIZE_WORKERS") else {}),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Baize 命令行入口。"""
    parser = argparse.ArgumentParser(prog="baize", description="白泽·智脑 (Baize) 命令行")
    parser.add_argument("--version", action="version", version="baize 2.0.1")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="启动 Web API 服务（默认）")
    sub.add_parser("doctor", help="环境自检：系统工具/模型/向量/浏览器依赖")
    args, rest = parser.parse_known_args(argv)

    if args.command == "doctor":
        return cmd_doctor(rest)
    return cmd_serve(rest)


if __name__ == "__main__":
    sys.exit(main())
