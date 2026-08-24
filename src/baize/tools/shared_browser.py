"""共享协作浏览器 — 人机共用的有头持久化浏览器。

与 :mod:`baize.tools.browser_tools`（静默无头浏览器）不同，本模块提供
**一个长期存活、人机共用**的浏览器实例：

- **有头可视化**：默认以有头模式启动 Chromium（窗口可见），人工可直接在
  桌面上观察并操作同一窗口（扫码登录、输入验证码、绕过验证、点击确认等）；
  无图形界面环境（无 ``DISPLAY``）自动降级为无头，配合前端「共享浏览器」
  面板的实时截图查看。
- **登录态持久化**：使用 ``launch_persistent_context`` + 固定
  ``user_data_dir``（默认 ``~/.baize/shared-browser-profile``），Cookie /
  LocalStorage 跨会话保留 —— 人工登录一次，AI 后续操作全部复用登录态。
- **人工介入工具** ``shared_browser_wait_user``：AI 打开登录页后阻塞等待
  人工扫码/登录/操作，可配置成功跳转 URL 前缀自动判定，或由前端面板点击
  「我已完成」手动放行；超时后返回并允许 AI 再次调用续等。
- **可中断**：工具执行被取消（超时 / 用户中断）时不会泄漏浏览器进程
  （共享实例保持存活，仅释放操作锁）。

典型流程（渗透测试需要登录的扫描场景）::

    1. shared_browser_open("https://target/login")
    2. shared_browser_wait_user("请扫码登录，完成后我继续",
                                 success_url_prefix="https://target/console")
    3. （人工在浏览器窗口扫码 / 前端面板查看并点击「我已完成」）
    4. AI 继续用 shared_browser_open / shared_browser_snapshot 等复用登录态

**授权声明**：仅用于已获授权的 Web 应用测试。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import base64
from typing import Any, Awaitable, Callable, Optional

from baize.tools.extended import _check_url_allowed
from baize.tools.registry import register_tool

logger = logging.getLogger("baize.tools.shared_browser")

DEFAULT_PROFILE = Path.home() / ".baize" / "shared-browser-profile"

# 固定视口尺寸：前端截图交互层按此比例将屏幕坐标映射为页面坐标。
# 截图默认截取视口区域，与 viewport 严格一致，保证点击坐标精确。
VIEWPORT_WIDTH = 1366
VIEWPORT_HEIGHT = 768


def _resolve_headless() -> bool:
    """决定共享浏览器是否以无头模式运行。

    优先级：环境变量 ``BAIZE_SHARED_BROWSER_HEADLESS``（1/0/true/false）；
    未设置时：有图形界面（DISPLAY / WAYLAND_DISPLAY）则默认有头，否则无头。
    """
    env = os.environ.get("BAIZE_SHARED_BROWSER_HEADLESS")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    if os.name == "nt":
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class SharedBrowserManager:
    """全局唯一的共享协作浏览器管理器（线程安全的单例）。"""

    _instance: Optional["SharedBrowserManager"] = None

    def __init__(self) -> None:
        self._playwright: Any = None
        self._playwright_run: Any = None
        self._context: Any = None
        self._lock = asyncio.Lock()
        self._confirm_event = asyncio.Event()
        self._headless: bool = _resolve_headless()
        self._profile: Path = Path(
            os.environ.get("BAIZE_SHARED_BROWSER_PROFILE") or DEFAULT_PROFILE
        )
        # CDP 实时串流与输入注入
        self._cdp: Any = None
        self._cdp_page: Any = None
        self._stream_cdp: Any = None
        self._stream_handler: Any = None
        self._stream_mode: Optional[str] = None
        self._stream_task: Any = None

    # ---- 单例 -----------------------------------------------------------
    @classmethod
    def get(cls) -> "SharedBrowserManager":
        if cls._instance is None:
            cls._instance = SharedBrowserManager()
        return cls._instance

    # ---- 生命周期 -------------------------------------------------------
    def _ensure_playwright(self) -> Any:
        if self._playwright is None:
            try:
                from playwright.async_api import async_playwright  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "共享浏览器需要 playwright 库。请先安装: "
                    "pip install playwright && playwright install chromium"
                ) from exc
            self._playwright = async_playwright()
        return self._playwright

    async def _ensure_started(self) -> Any:
        """惰性启动持久化浏览器上下文，返回可操作页面。"""
        if self._context is None:
            if self._playwright_run is None:
                p = self._ensure_playwright()
                self._playwright_run = await p.start()
            self._profile.mkdir(parents=True, exist_ok=True)
            self._context = await self._playwright_run.chromium.launch_persistent_context(
                str(self._profile),
                headless=self._headless,
                args=["--no-sandbox", "--disable-gpu"],
                viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            )
            logger.info(
                "共享浏览器已启动（headless=%s, profile=%s）",
                self._headless, self._profile,
            )
        pages = self._context.pages
        if pages:
            return pages[-1]
        return await self._context.new_page()

    def _page_or_raise(self) -> Any:
        if self._context is None:
            raise RuntimeError("共享浏览器未启动（请先调用 shared_browser_open）")
        pages = self._context.pages
        if not pages:
            raise RuntimeError("共享浏览器窗口已关闭（请调用 shared_browser_open 重新导航）")
        return pages[-1]

    async def open(self, url: str) -> str:
        """在共享浏览器中导航到指定 URL（复用同一窗口与登录态）。"""
        _check_url_allowed(url, allow_internal=False)
        async with self._lock:
            page = await self._ensure_started()
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            return f"已打开: {page.url}（标题: {await page.title()}）"

    async def snapshot(self) -> Optional[bytes]:
        """截取共享浏览器当前页面（PNG bytes），未启动时返回 None。"""
        if self._context is None:
            return None
        try:
            page = self._page_or_raise()
            return await page.screenshot(type="png")
        except Exception:  # noqa: BLE001
            return None

    async def save_snapshot(self, output_path: str) -> str:
        """截取当前页面并保存到指定路径。"""
        img = await self.snapshot()
        if img is None:
            return "[shared_browser_snapshot] 共享浏览器未启动或窗口已关闭"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(img)
        return f"截图已保存: {output_path}"

    def confirm(self) -> None:
        """人工确认放行（供前端面板「我已完成」按钮调用）。"""
        self._confirm_event.set()
        logger.info("共享浏览器：收到人工确认信号")

    async def wait_for_user(
        self,
        message: str,
        timeout: int = 240,
        success_url_prefix: Optional[str] = None,
    ) -> str:
        """阻塞等待人工介入（扫码 / 登录 / 验证码 / 手动确认）。

        判定条件（任一满足即返回）:
        1. 前端面板点击「我已完成」（confirm）；
        2. 当前页面 URL 以 ``success_url_prefix`` 开头（自动检测登录成功跳转）。

        被外层取消（任务中断 / 300s 工具超时）时抛 ``asyncio.CancelledError``，
        共享浏览器实例保持存活不关闭；等待超时则返回提示，AI 可再次调用续等。
        """
        start = asyncio.get_running_loop().time()
        deadline = start + max(timeout, 10)
        while True:
            if self._confirm_event.is_set():
                self._confirm_event.clear()
                return f"[人工确认] {message}"
            try:
                page = self._page_or_raise()
                if success_url_prefix and page.url.startswith(success_url_prefix):
                    return f"[登录态检测] 已跳转到 {page.url}，判定人工操作完成"
            except Exception:  # noqa: BLE001
                pass
            if asyncio.get_running_loop().time() >= deadline:
                return (
                    f"[等待超时] 已等待 {timeout}s 未见人工操作完成。"
                    "请人工在共享浏览器完成扫码/登录后，让 AI 再次调用本工具续等。"
                )
            await asyncio.sleep(1)

    async def click(self, selector: str) -> str:
        async with self._lock:
            page = self._page_or_raise()
            el = await page.wait_for_selector(selector, timeout=15000)
            if el is None:
                return f"[shared_browser_click] 未找到元素: {selector}"
            await el.click()
            await page.wait_for_load_state("domcontentloaded")
            return f"已点击 {selector}，当前 URL: {page.url}"

    async def fill(self, selector: str, value: str) -> str:
        async with self._lock:
            page = self._page_or_raise()
            el = await page.wait_for_selector(selector, timeout=15000)
            if el is None:
                return f"[shared_browser_fill] 未找到元素: {selector}"
            await el.fill(value)
            return f"已向 {selector} 填写值"

    async def evaluate(self, script: str) -> str:
        async with self._lock:
            page = self._page_or_raise()
            result = await page.evaluate(script)
            if isinstance(result, (dict, list)):
                import json

                return json.dumps(result, ensure_ascii=False, indent=2)
            return str(result)

    async def click_viewport(
        self,
        x: float,
        y: float,
        button: str = "left",
        click_count: int = 1,
    ) -> str:
        """按视口坐标点击（前端截图交互层映射后的坐标）。

        坐标以固定视口 (VIEWPORT_WIDTH x VIEWPORT_HEIGHT) 为参考系，
        与截图像素位置一一对应。
        """
        async with self._lock:
            page = self._page_or_raise()
            await page.mouse.click(
                x, y, button=button, click_count=max(1, click_count)
            )
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            return f"已点击 ({x:.0f},{y:.0f})，当前 URL: {page.url}"

    async def type_text(self, text: str) -> str:
        """向当前聚焦元素输入文本（逐字符模拟真实键盘输入）。"""
        async with self._lock:
            page = self._page_or_raise()
            await page.keyboard.type(text)
            return f"已输入 {len(text)} 个字符"

    async def press_key(self, key: str) -> str:
        """按下指定按键（Enter/Tab/Backspace/Escape/箭头 等 Playwright 键名）。"""
        async with self._lock:
            page = self._page_or_raise()
            await page.keyboard.press(key)
            return f"已按键: {key}"

    async def scroll(self, delta_x: float = 0, delta_y: float = 0) -> str:
        """滚动当前页面（相对滚动量，相当于鼠标滚轮）。"""
        async with self._lock:
            page = self._page_or_raise()
            await page.mouse.wheel(delta_x, delta_y)
            await page.wait_for_timeout(150)
            return f"已滚动 (dx={delta_x}, dy={delta_y})"

    async def nav(self, action: str) -> str:
        """浏览器导航动作: back / forward / reload。"""
        action = action.lower()
        if action not in ("back", "forward", "reload"):
            raise ValueError(f"不支持的导航动作: {action}（支持 back/forward/reload）")
        async with self._lock:
            page = self._page_or_raise()
            if action == "back":
                await page.go_back()
            elif action == "forward":
                await page.go_forward()
            else:
                await page.reload(wait_until="domcontentloaded")
            return f"导航 [{action}] 完成，当前 URL: {page.url}"

    async def close(self) -> str:
        """关闭共享浏览器（保留 user_data_dir 中的登录态）。"""
        async with self._lock:
            if self._context is not None:
                try:
                    await self._context.close()
                except Exception:  # noqa: BLE001
                    pass
                self._context = None
            if self._playwright_run is not None:
                try:
                    await self._playwright_run.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._playwright_run = None
            self._cdp = None
            self._cdp_page = None
            self._stream_cdp = None
            self._stream_handler = None
            self._stream_mode = None
            self._stream_task = None
            self._confirm_event.clear()
            return "共享浏览器已关闭（登录态已保留，可随时重新打开）"

    def status(self) -> dict[str, Any]:
        """返回共享浏览器当前状态（供前端面板展示，同步方法）。"""
        url = ""
        if self._context is not None:
            try:
                page = self._page_or_raise()
                url = page.url
            except Exception:  # noqa: BLE001
                pass
        return {
            "running": self._context is not None,
            "headless": bool(self._headless),
            "url": url,
            "profile": str(self._profile),
            "confirm_pending": self._confirm_event.is_set(),
            "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        }

    # ---- CDP 实时串流 + 输入注入 ----------------------------------------
    def is_running(self) -> bool:
        """共享浏览器是否已启动。"""
        return self._context is not None

    def is_streaming(self) -> bool:
        """是否正在推送实时帧流。"""
        return self._stream_mode is not None

    async def get_cdp_session(self) -> Any:
        """获取当前 page 的 CDPSession（惰性创建；page 变化时重建）。"""
        page = self._page_or_raise()
        if self._cdp is None or self._cdp_page is not page:
            self._cdp = await page.context.new_cdp_session(page)
            self._cdp_page = page
        return self._cdp

    async def start_stream(
        self,
        send_frame: Callable[[bytes, int, int], Awaitable[None]],
        send_status: Callable[[], Awaitable[None]],
    ) -> None:
        """启动 CDP startScreencast 实时帧流；每帧通过 send_frame 推出。

        CDP screencast 不可用时降级为后端循环截图推帧（~15fps）。
        本方法注册事件 handler 后立即返回（事件驱动），不阻塞调用方。
        """
        cdp = await self.get_cdp_session()
        self._stream_cdp = cdp

        async def _on_frame(params: dict) -> None:
            try:
                data = base64.b64decode(params.get("data", ""))
                meta = params.get("metadata", {}) or {}
                w = int(meta.get("width", VIEWPORT_WIDTH))
                h = int(meta.get("height", VIEWPORT_HEIGHT))
                await send_frame(data, w, h)
            except Exception:  # noqa: BLE001
                pass
            try:
                await cdp.send(
                    "Page.screencastFrameAck",
                    {"sessionId": params.get("sessionId")},
                )
            except Exception:  # noqa: BLE001
                pass

        def _handler(params: dict) -> None:
            asyncio.create_task(_on_frame(params))

        self._stream_handler = _handler
        cdp.on("Page.screencastFrame", _handler)
        try:
            await cdp.send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": 70,
                    "maxWidth": VIEWPORT_WIDTH,
                    "maxHeight": VIEWPORT_HEIGHT,
                    "everyNthFrame": 1,
                },
            )
            self._stream_mode = "cdp"
        except Exception as exc:  # noqa: BLE001
            logger.warning("CDP startScreencast 失败，降级为循环截图：%s", exc)
            self._stream_mode = "loop"
            self._stream_task = asyncio.create_task(self._loop_screencast(send_frame))

    async def _loop_screencast(
        self,
        send_frame: Callable[[bytes, int, int], Awaitable[None]],
    ) -> None:
        """降级方案：后端循环截图推帧（~15fps）。stop_stream 将 _stream_mode 置空退出。"""
        while self._stream_mode == "loop":
            try:
                page = self._page_or_raise()
                data = await page.screenshot(type="jpeg", quality=70)
                await send_frame(data, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1 / 15)

    async def stop_stream(self) -> None:
        """停止帧流。"""
        cdp = self._stream_cdp
        mode = self._stream_mode
        task = self._stream_task
        self._stream_cdp = None
        self._stream_handler = None
        self._stream_mode = None
        self._stream_task = None
        if mode == "cdp" and cdp is not None:
            try:
                await cdp.send("Page.stopScreencast")
            except Exception:  # noqa: BLE001
                pass
        if task is not None:
            task.cancel()

    # ---- 输入注入（CDP Input.dispatch* —— 参考 browser-use 直接 CDP 方案）----
    async def _ensure_cdp(self) -> Any:
        """获取或创建 CDP session（输入注入专用，与 screencast 共用同一个 session）。"""
        page = self._page_or_raise()
        if self._cdp is None or self._cdp_page is not page:
            self._cdp = await page.context.new_cdp_session(page)
            self._cdp_page = page
        return self._cdp

    async def input_mouse_move(self, x: float, y: float) -> None:
        async with self._lock:
            cdp = await self._ensure_cdp()
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": x, "y": y,
            })

    async def input_mouse_down(self, x: float, y: float, button: str = "left", click_count: int = 1) -> None:
        async with self._lock:
            cdp = await self._ensure_cdp()
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": button, "clickCount": click_count,
            })

    async def input_mouse_up(self, x: float, y: float, button: str = "left", click_count: int = 1) -> None:
        async with self._lock:
            cdp = await self._ensure_cdp()
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": button, "clickCount": click_count,
            })

    async def input_click(
        self, x: float, y: float, button: str = "left", click_count: int = 1
    ) -> None:
        async with self._lock:
            cdp = await self._ensure_cdp()
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": button, "clickCount": click_count,
            })
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": button, "clickCount": click_count,
            })

    async def input_wheel(self, delta_x: float = 0, delta_y: float = 0) -> None:
        async with self._lock:
            cdp = await self._ensure_cdp()
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": 0, "y": 0,
                "deltaX": delta_x, "deltaY": delta_y,
            })

    async def input_key(self, key: str) -> None:
        async with self._lock:
            await self._page_or_raise().keyboard.press(key)

    async def input_type(self, text: str) -> None:
        async with self._lock:
            await self._page_or_raise().keyboard.type(text, delay=0)


def get_shared_browser() -> SharedBrowserManager:
    """返回全局共享浏览器管理器单例。"""
    return SharedBrowserManager.get()


# ===========================================================================
# 工具注册（供 LLM 调用）
# ===========================================================================

@register_tool(
    description=(
        "[共享协作浏览器] 在持久化的有头浏览器窗口中打开指定 URL。"
        "该浏览器人机共用：窗口可见、登录态（Cookie/LocalStorage）跨会话保留。"
        "适合需要登录后才能扫描的目标：先用本工具打开登录页，再用 "
        "shared_browser_wait_user 等待人工扫码登录，之后所有操作均复用登录态。"
        "参数: url 目标 URL。返回打开后的最终 URL 与页面标题。"
    ),
    category="security",
    tags=["browser", "shared", "login", "playwright"],
)
async def shared_browser_open(url: str) -> str:
    """打开共享浏览器并导航到 URL（复用登录态）。"""
    try:
        return await get_shared_browser().open(url)
    except Exception as exc:  # noqa: BLE001
        return f"[shared_browser_open 失败] {exc}"


@register_tool(
    description=(
        "[共享协作浏览器] 阻塞等待人工介入 —— 人工在共享浏览器窗口中完成"
        "扫码登录 / 输入验证码 / 绕过验证 / 手动确认等 AI 无法自动完成的操作。"
        "参数: message 给人工的操作提示（如『请扫码登录，完成后我继续』）;"
        "timeout 等待秒数(默认 240，超时后返回，可再次调用续等);"
        "success_url_prefix 登录成功后跳转的 URL 前缀(如 'https://t/console')，"
        "检测到 URL 匹配即判定人工操作完成并自动返回。"
        "也可由人工在前端「共享浏览器」面板点击『我已完成』立即放行。"
    ),
    category="security",
    tags=["browser", "shared", "login", "human-in-the-loop"],
)
async def shared_browser_wait_user(
    message: str,
    timeout: int = 240,
    success_url_prefix: Optional[str] = None,
) -> str:
    """等待人工完成扫码/登录等操作后继续。"""
    try:
        return await get_shared_browser().wait_for_user(
            message, timeout=timeout, success_url_prefix=success_url_prefix
        )
    except asyncio.CancelledError:
        # 被中断/超时取消：共享浏览器保持存活，告知调用方等待被中断
        raise
    except Exception as exc:  # noqa: BLE001
        return f"[shared_browser_wait_user 失败] {exc}"


@register_tool(
    description=(
        "[共享协作浏览器] 对共享浏览器当前页面截图并保存到本地路径"
        "（AI 查看当前页面状态，确认登录态/页面渲染是否正确）。"
        "参数: output_path 截图保存路径(如 /tmp/shared_login.png);"
        "返回截图保存路径。"
    ),
    category="security",
    tags=["browser", "shared", "screenshot"],
)
async def shared_browser_snapshot(output_path: str) -> str:
    """保存共享浏览器当前页面截图。"""
    try:
        return await get_shared_browser().save_snapshot(output_path)
    except Exception as exc:  # noqa: BLE001
        return f"[shared_browser_snapshot 失败] {exc}"


@register_tool(
    description=(
        "[共享协作浏览器] 点击共享浏览器当前页面中指定 CSS 选择器的元素，"
        "返回点击后的 URL。用于登录后进入功能页、触发下载、提交表单等。"
        "参数: selector CSS 选择器(如 '#btn' / 'a[href*=console]')。"
    ),
    category="security",
    tags=["browser", "shared", "click"],
)
async def shared_browser_click(selector: str) -> str:
    """点击共享浏览器当前页面的元素。"""
    try:
        return await get_shared_browser().click(selector)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return f"[shared_browser_click 失败] {exc}"


@register_tool(
    description=(
        "[共享协作浏览器] 在共享浏览器当前页面指定输入框（CSS 选择器）填写值，"
        "用于 AI 辅助填表（如账号密码之外的表单项）。"
        "参数: selector CSS 选择器(如 'input[name=name]'); value 要填写的值。"
    ),
    category="security",
    tags=["browser", "shared", "form"],
)
async def shared_browser_fill(selector: str, value: str) -> str:
    """填写共享浏览器当前页面的输入框。"""
    try:
        return await get_shared_browser().fill(selector, value)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return f"[shared_browser_fill 失败] {exc}"


@register_tool(
    description=(
        "[共享协作浏览器] 在共享浏览器当前页面上下文执行 JS 表达式并返回结果"
        "（读取登录后页面的动态数据 / 检查页面状态）。"
        "参数: script JS 表达式(如 'document.title' / 'JSON.stringify(cookies)')。"
    ),
    category="security",
    tags=["browser", "shared", "js"],
)
async def shared_browser_evaluate(script: str) -> str:
    """在共享浏览器当前页面执行 JS。"""
    try:
        return await get_shared_browser().evaluate(script)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return f"[shared_browser_evaluate 失败] {exc}"


@register_tool(
    description=(
        "[共享协作浏览器] 查询共享浏览器状态：是否运行、headless 模式、"
        "当前 URL、浏览器配置目录。用于确认共享浏览器可用性。"
    ),
    category="security",
    tags=["browser", "shared", "status"],
)
async def shared_browser_status() -> str:
    """查询共享浏览器状态。"""
    try:
        import json

        st = get_shared_browser().status()
        return json.dumps(st, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return f"[shared_browser_status 失败] {exc}"


@register_tool(
    description=(
        "[共享协作浏览器] 关闭共享浏览器（登录态保留在磁盘，可随时重新打开）。"
        "用于任务结束后的资源清理。"
    ),
    category="security",
    tags=["browser", "shared", "cleanup"],
)
async def shared_browser_close() -> str:
    """关闭共享浏览器（保留登录态）。"""
    try:
        return await get_shared_browser().close()
    except Exception as exc:  # noqa: BLE001
        return f"[shared_browser_close 失败] {exc}"


__all__ = [
    "SharedBrowserManager",
    "get_shared_browser",
    "shared_browser_open",
    "shared_browser_wait_user",
    "shared_browser_snapshot",
    "shared_browser_click",
    "shared_browser_fill",
    "shared_browser_evaluate",
    "shared_browser_status",
    "shared_browser_close",
]
