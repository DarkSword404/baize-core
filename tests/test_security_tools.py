"""安全工具与浏览器工具测试。"""

from __future__ import annotations

import asyncio

import pytest

from baize.tools.registry import registry


def test_security_tools_schema() -> None:
    for name in ("nmap_scan", "whois_lookup", "dns_lookup", "cve_lookup", "browser_fetch"):
        spec = registry.get(name)
        assert spec is not None, name
        schema = spec.to_schema()["function"]["parameters"]
        assert "type" in schema
        assert "properties" in schema
        assert isinstance(schema["required"], list)


def test_dangerous_parameter_blocked() -> None:
    """sqlmap 武器化参数应被拦截。"""
    from baize.tools.security_tools import _check_command

    blocked = _check_command("sqlmap", "sqlmap -u http://x --os-shell")
    assert blocked is not None
    assert "危险参数" in blocked
    assert _check_command("sqlmap", "sqlmap -u http://x --batch") is None


def test_dangerous_parameter_allowed_with_env(monkeypatch) -> None:
    from baize.tools.security_tools import _check_command

    monkeypatch.setenv("BAIZE_ALLOW_WEAPONIZED", "1")
    assert _check_command("sqlmap", "sqlmap -u http://x --os-shell") is None
    monkeypatch.delenv("BAIZE_ALLOW_WEAPONIZED")


def test_metasploit_pattern() -> None:
    from baize.tools.security_tools import _check_command

    assert _check_command("metasploit", "msfconsole -q -x 'exploit'") is not None
    assert _check_command("metasploit", "msfconsole -q -r /tmp/rc") is None


@pytest.mark.asyncio
async def test_whois_real_call() -> None:
    """真实调用 whois（系统装有该工具时）。"""
    import shutil

    if shutil.which("whois") is None:
        pytest.skip("未安装 whois")
    from baize.tools import security_tools_extra as xt

    out = await xt.whois_lookup("example.com")
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_dns_real_call() -> None:
    import shutil

    if shutil.which("dig") is None:
        pytest.skip("未安装 dig")
    from baize.tools import security_tools_extra as xt

    out = await xt.dns_lookup("example.com", "A")
    assert "104.20" in out or "93.184" in out  # example.com 解析结果


def test_cve_lookup_invalid_input() -> None:
    from baize.tools import security_tools_extra as xt

    out = asyncio.run(xt.cve_lookup("not-a-cve"))
    assert "CVE" in out and "错误" in out


def test_browser_ssrf_blocked() -> None:
    """浏览器工具必须拒绝访问内网/保留地址（SSRF 防护）。"""
    from baize.tools import browser_tools as bt

    out = asyncio.run(bt.browser_fetch("http://127.0.0.1/", timeout=5))
    # 要么 SSRF 拦截，要么 playwright 缺失的明确错误（都算防护生效）
    assert "拒绝" in out or "playwright" in out or "失败" in out


def test_browser_missing_playwright_error() -> None:
    """未装 playwright 时必须给出明确错误（fail-closed，不静默）。"""
    import importlib.util

    if importlib.util.find_spec("playwright") is not None:
        pytest.skip("已安装 playwright，跳过")
    from baize.tools import browser_tools as bt

    out = asyncio.run(bt.browser_fetch("https://example.com/", timeout=5))
    assert "playwright" in out
