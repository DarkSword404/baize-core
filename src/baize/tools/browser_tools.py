"""Baize 浏览器自动化工具集 — Web 侦察与页面交互。

基于 Playwright（Chromium 无头浏览器）封装常用浏览器能力，供智能体
对 Web 目标做自动化侦察与交互:
- ``browser_fetch``: 打开页面并提取结构化信息（标题 / 链接 / 表单 / 文本摘要）
- ``browser_screenshot``: 页面截图（保存到指定路径）
- ``browser_click``: 点击页面元素
- ``browser_fill``: 填写表单输入框
- ``browser_evaluate``: 在页面上下文执行 JS 表达式

**SSRF 防护**: 所有 URL 目标复用 ``extended._check_url_allowed`` 校验
（禁止访问内网/保留地址，除非显式 ``BAIZE_FETCH_ALLOW_INTERNAL=1``）。

**依赖说明**: 需安装 ``playwright`` 并执行 ``playwright install chromium``。
未安装时工具会返回明确的错误提示（fail-closed，不做静默降级）。

**授权声明**: 仅用于已获授权的 Web 应用测试。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from baize.tools.extended import _check_url_allowed
from baize.tools.registry import register_tool

logger = logging.getLogger("baize.tools.browser")


def _require_playwright() -> Any:
    """惰性加载 Playwright；不可用/未装浏览器时抛出 RuntimeError（fail-closed）。"""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "浏览器工具需要 playwright 库。请先安装: "
            "pip install playwright && playwright install chromium"
        ) from exc
    return async_playwright


def _check_target(url: str) -> None:
    """校验目标 URL（SSRF 防护）。"""
    try:
        _check_url_allowed(url, allow_internal=False)
    except ValueError as exc:
        raise ValueError(f"浏览器工具拒绝访问该目标: {exc}") from exc


async def _open_page(url: str, timeout: int = 30, headless: bool = True) -> Any:
    """启动无头浏览器并打开页面，返回 (playwright 上下文, 浏览器, 页面)。"""
    async_playwright = _require_playwright()
    _check_target(url)
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-gpu"])
    page = await browser.new_page()
    try:
        await page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
    except Exception as exc:  # noqa: BLE001
        await browser.close()
        await p.stop()
        raise RuntimeError(f"打开页面失败: {exc}") from exc
    return p, browser, page


@register_tool(
    description=(
        "使用无头浏览器打开目标页面并提取结构化侦察信息：页面标题、最终 URL、"
        "响应状态、页面链接列表(含 href/文本)、表单元素(输入框/下拉/按钮)、"
        "可见文本摘要。参数: url 目标 URL; max_links 最多返回的链接数(默认 30);"
        "正文摘要长度 max_text(默认 800)。适合 Web 侦察、链接挖掘、表单分析。"
    ),
    category="security",
    tags=["browser", "recon", "web", "playwright"],
)
async def browser_fetch(
    url: str,
    max_links: int = 30,
    max_text: int = 800,
    timeout: int = 30,
) -> str:
    """无头浏览器页面侦察（标题/链接/表单/文本）。"""
    try:
        p, browser, page = await _open_page(url, timeout=timeout)
        try:
            title = await page.title()
            final_url = page.url
            links: list[str] = []
            anchors = await page.query_selector_all("a[href]")
            for a in anchors[: max_links * 2]:
                href = await a.get_attribute("href")
                if not href or href.startswith(("javascript:", "#", "mailto:")):
                    continue
                text = (await a.inner_text()).strip() or ""
                links.append(f"{href}  [{text[:40]}]" if text else href)
                if len(links) >= max_links:
                    break
            forms: list[str] = []
            f_els = await page.query_selector_all("form")
            for f in f_els[:10]:
                action = await f.get_attribute("action") or ""
                method = (await f.get_attribute("method")) or "GET"
                inputs = await f.query_selector_all("input, textarea, select")
                fields = []
                for i in inputs[:15]:
                    i_type = await i.get_attribute("type") or "text"
                    i_name = await i.get_attribute("name") or ""
                    i_id = await i.get_attribute("id") or ""
                    fields.append(f"{i_type}:{i_name or i_id}")
                forms.append(f"<form {method} {action}> fields=[{', '.join(fields)}]")
            body_text = (await page.inner_text("body")).strip().replace("\n", " ")[:max_text]
            parts = [
                f"标题: {title}",
                f"最终URL: {final_url}",
                f"链接({len(links)}):",
            ] + [f"  - {l}" for l in links] + [
                f"表单({len(forms)}):",
            ] + [f"  - {f}" for f in forms] + [
                f"文本摘要: {body_text or '(空页面)'}",
            ]
            return "\n".join(parts)
        finally:
            await browser.close()
            await p.stop()
    except Exception as exc:  # noqa: BLE001
        return f"[browser_fetch 失败] {exc}"


@register_tool(
    description=(
        "使用无头浏览器对目标页面截图并保存到本地路径。"
        "参数: url 目标 URL; output_path 截图保存路径(如 /tmp/page.png);"
        "full_page 是否截取整页(默认否，只截可视区域); 返回截图保存路径。"
    ),
    category="security",
    tags=["browser", "screenshot", "web"],
)
async def browser_screenshot(
    url: str,
    output_path: str,
    full_page: bool = False,
    timeout: int = 30,
) -> str:
    """浏览器截图保存到指定路径。"""
    try:
        p, browser, page = await _open_page(url, timeout=timeout)
        try:
            await page.screenshot(path=output_path, full_page=full_page)
            return f"截图已保存: {output_path}"
        finally:
            await browser.close()
            await p.stop()
    except Exception as exc:  # noqa: BLE001
        return f"[browser_screenshot 失败] {exc}"


@register_tool(
    description=(
        "在目标页面点击指定 CSS 选择器元素，然后返回页面标题与关键信息变化。"
        "参数: url 目标 URL; selector CSS 选择器(如 '#login-btn' 或 'a[href*=signup]');"
        "返回点击后的页面标题与文本摘要。"
    ),
    category="security",
    tags=["browser", "click", "web", "automation"],
)
async def browser_click(
    url: str,
    selector: str,
    max_text: int = 600,
    timeout: int = 30,
) -> str:
    """点击页面元素并返回变化后的信息。"""
    try:
        p, browser, page = await _open_page(url, timeout=timeout)
        try:
            el = await page.wait_for_selector(selector, timeout=timeout * 1000)
            if el is None:
                return f"未找到元素: {selector}"
            await el.click()
            await page.wait_for_load_state("domcontentloaded")
            title = await page.title()
            body_text = (await page.inner_text("body")).strip().replace("\n", " ")[:max_text]
            return f"点击后标题: {title}\nURL: {page.url}\n文本: {body_text or '(空页面)'}"
        finally:
            await browser.close()
            await p.stop()
    except Exception as exc:  # noqa: BLE001
        return f"[browser_click 失败] {exc}"


@register_tool(
    description=(
        "在目标页面的输入框（CSS 选择器指定）填写指定值，然后返回确认。"
        "参数: url 目标 URL; selector CSS 选择器(如 'input[name=username]');"
        "value 要填写的值; 返回填写结果确认。"
    ),
    category="security",
    tags=["browser", "form", "web", "automation"],
)
async def browser_fill(
    url: str,
    selector: str,
    value: str,
    timeout: int = 30,
) -> str:
    """填写表单输入框。"""
    try:
        p, browser, page = await _open_page(url, timeout=timeout)
        try:
            el = await page.wait_for_selector(selector, timeout=timeout * 1000)
            if el is None:
                return f"未找到元素: {selector}"
            await el.fill(value)
            return f"已向 {selector} 填写: {value}"
        finally:
            await browser.close()
            await p.stop()
    except Exception as exc:  # noqa: BLE001
        return f"[browser_fill 失败] {exc}"


@register_tool(
    description=(
        "在目标页面上下文执行 JS 表达式并返回结果（用于动态页面数据提取）。"
        "参数: url 目标 URL; script JS 表达式(如 'document.title' 或 "
        "'JSON.stringify(Object.keys(window))'); 返回执行结果字符串。"
    ),
    category="security",
    tags=["browser", "js", "web", "automation"],
)
async def browser_evaluate(
    url: str,
    script: str,
    timeout: int = 30,
) -> str:
    """在页面上下文执行 JS 表达式。"""
    try:
        p, browser, page = await _open_page(url, timeout=timeout)
        try:
            result = await page.evaluate(script)
            if isinstance(result, (dict, list)):
                import json

                return json.dumps(result, ensure_ascii=False, indent=2)
            return str(result)
        finally:
            await browser.close()
            await p.stop()
    except Exception as exc:  # noqa: BLE001
        return f"[browser_evaluate 失败] {exc}"


__all__ = [
    "browser_fetch",
    "browser_screenshot",
    "browser_click",
    "browser_fill",
    "browser_evaluate",
]
