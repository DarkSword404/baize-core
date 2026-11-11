"""Baize 有状态 HTTP 会话工具（登录态复用）。

``http_request`` 是**无状态**的：每次调用新建连接、不保留 Cookie/鉴权头，
因此需要登录的测试场景（先 POST /login 拿会话，再访问受保护接口）无法复用
登录态。本模块补齐这一缺口：

- 以 ``session`` 名为单位维护 Cookie / 默认请求头 / Basic 鉴权；
- 每次请求自动带上并合并响应 Set-Cookie，实现「登录 → 复用」；
- 会话状态落盘到当前会话工作区 ``http_sessions.json``，
  进程重启/会话续跑后仍可恢复（长任务可续跑）；
- SSRF 校验复用 ``extended._check_url_allowed``，并对每次重定向逐跳校验。

通过 ``register_tool`` 注册，自动出现在 agent 的按需工具目录中；
工具名为 ``http_session_request``。是否使用、用哪个会话名由 AI 自行判断。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional
from pathlib import Path

import httpx

from baize.tools.extended import _check_url_allowed
from baize.tools.registry import register_tool

logger = logging.getLogger("baize.tools.session")

_SESSIONS_FILENAME = "http_sessions.json"
_REDIRECT_CODES = (301, 302, 303, 307, 308)

_lock = threading.RLock()
# 无工作区上下文（CLI/测试）时的进程内回退存储
_memory_sessions: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# 会话状态存储（工作区落盘 + 进程内回退）
# ---------------------------------------------------------------------------

def _session_file() -> Optional[Path]:
    """会话状态文件路径；无会话工作区时返回 None（走进程内存储）。"""
    try:
        from baize.pentest.workspace import get_current_workspace

        ws = get_current_workspace()
        if ws is None:
            return None
        return ws / _SESSIONS_FILENAME
    except Exception:  # noqa: BLE001
        logger.debug("解析会话状态路径失败", exc_info=True)
        return None


def _load_store() -> tuple[dict[str, Any], Optional[Path]]:
    path = _session_file()
    if path is None:
        return _memory_sessions, None
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                return data, path
        except Exception:  # noqa: BLE001
            logger.debug("读取会话状态失败 %s", path, exc_info=True)
    return {}, path


def _save_store(store: dict[str, Any], path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.debug("写入会话状态失败 %s", path, exc_info=True)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _new_session() -> dict[str, Any]:
    return {
        "cookies": {},
        "headers": {},
        "auth": "",
        "created_at": _now(),
        "updated_at": _now(),
        "request_count": 0,
        "last_url": "",
    }


def _get_session(store: dict[str, Any], name: str) -> dict[str, Any]:
    sess = store.get(name)
    if not isinstance(sess, dict):
        sess = _new_session()
        store[name] = sess
    # 兼容/纠偏旧结构
    sess.setdefault("cookies", {})
    sess.setdefault("headers", {})
    sess.setdefault("auth", "")
    sess.setdefault("request_count", 0)
    sess.setdefault("last_url", "")
    return sess


def _parse_auth(auth: Optional[str]) -> Any:
    """把 "user:pass" 解析为 httpx BasicAuth 元组；无值返回 None。"""
    if not auth:
        return None
    if ":" in auth:
        user, pwd = auth.split(":", 1)
        return (user, pwd)
    return None


# ---------------------------------------------------------------------------
# 核心请求（手动重定向 + 逐跳 SSRF 校验）
# ---------------------------------------------------------------------------

def _perform_request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    cookies: dict[str, str],
    auth: Any,
    content: Optional[str],
    follow_redirects: bool,
    timeout: int,
) -> tuple[Any, str, str, list[str], dict[str, str]]:
    """执行请求，返回 (resp, final_url, final_method, redirect_hops, updated_cookies)。

    逐跳校验 SSRF；自动在各跳之间合并 Set-Cookie（登录态跨重定向保持）。
    """
    allow_internal = os.getenv("BAIZE_FETCH_ALLOW_INTERNAL", "").lower() in ("1", "true")
    cookie_jar = dict(cookies or {})
    hops: list[str] = []
    method_u = method.upper() or "GET"
    max_hops = 6 if follow_redirects else 1
    with httpx.Client(timeout=timeout, follow_redirects=False, verify=False) as client:
        current = url
        resp = None
        for _ in range(max_hops):
            _check_url_allowed(current, allow_internal)
            resp = client.request(
                method_u,
                current,
                headers=headers or {},
                cookies=cookie_jar or None,
                content=content,
                auth=auth,
            )
            # 合并本跳设置的 Cookie
            for ck_name, ck_val in resp.cookies.items():
                cookie_jar[ck_name] = ck_val
            if follow_redirects and resp.status_code in _REDIRECT_CODES:
                location = resp.headers.get("location")
                if not location:
                    break
                nxt = str(httpx.URL(current).join(location))
                hops.append(f"{resp.status_code} {current} -> {nxt}")
                # 303 或 POST 收到 301/302 → 按浏览器语义转为 GET
                if resp.status_code == 303 or (
                    resp.status_code in (301, 302) and method_u == "POST"
                ):
                    method_u = "GET"
                    content = None
                current = nxt
                continue
            break
        else:
            # 超过最大跳数仍未收敛
            return resp, current, method_u, hops, cookie_jar
    return resp, current, method_u, hops, cookie_jar


def _render_response(
    *,
    session_name: str,
    sess: dict[str, Any],
    resp: Any,
    final_url: str,
    final_method: str,
    hops: list[str],
    max_body: int,
) -> str:
    if resp is None:
        return "(无响应)"
    lines: list[str] = []
    lines.append(f"HTTP {resp.status_code} {final_method} {final_url}")
    lines.append(
        f"[会话] {session_name} | cookies={len(sess.get('cookies', {}))} "
        f"headers={len(sess.get('headers', {}))} "
        f"auth={'有' if sess.get('auth') else '无'}"
    )
    if hops:
        lines.append("[重定向] " + " ; ".join(hops))
    # 响应头节选（只挑与状态/会话/内容相关的）
    interesting = ("content-type", "server", "location", "set-cookie", "www-authenticate")
    header_lines = []
    for hk in interesting:
        for hv in resp.headers.get_list(hk):
            header_lines.append(f"{hk}: {hv}")
    if header_lines:
        lines.append("--- 响应头 ---")
        lines.extend(header_lines)
    body = resp.text[:max_body] if max_body and max_body > 0 else resp.text
    truncated = " (已截断)" if max_body and max_body > 0 and len(resp.text) > max_body else ""
    lines.append(f"--- 响应体{truncated} ---")
    lines.append(body)
    # 与 http_request 保持一致：HTML/JS 页面即使响应体截断，也从完整响应中
    # 额外提取 script/form/API 摘要，避免 agent 因 5000 字符上限看不到关键入口。
    ctype = str(resp.headers.get("content-type") or "").lower()
    full_text = resp.text or ""
    if (
        "html" in ctype
        or "javascript" in ctype
        or "<script" in full_text.lower()
        or "/api/" in full_text
    ):
        try:
            from baize.tools.security_tools_extra import extract_web_artifacts
            artifacts = extract_web_artifacts(full_text[:200_000], final_url)
            if artifacts and not artifacts.startswith("(未提取"):
                lines.append("--- 自动提取的 Web 证据 ---")
                lines.append(artifacts[:4000])
        except Exception:  # noqa: BLE001
            logger.debug("提取 Web 证据失败", exc_info=True)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 工具 1：有状态请求
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "发起**有状态** HTTP/HTTPS 请求：以 session 名为单位自动保存并复用 "
        "Cookie / 默认请求头 / Basic 鉴权，实现登录态跨调用复用。"
        "用于需要登录的测试（先 POST /login，再带会话访问受保护接口）。"
        "参数: session 会话名(自定义, 如 'target1'); url 目标 URL; "
        "method 请求方法(默认 GET); headers 本次请求头(可选, 覆盖同名默认头); "
        "cookies 本次附加 Cookie(可选); body 请求体(可选); "
        "auth Basic 鉴权 'user:pass'(可选, 不填则用会话已存鉴权); "
        "follow_redirects 是否跟随重定向(默认是, 逐跳做 SSRF 校验); "
        "max_body 响应体返回上限字节(默认 5000)。"
        "响应中的 Set-Cookie 会自动并入会话并在后续调用中携带。"
    ),
    category="security",
    tags=["web", "http", "session", "auth", "cookie"],
)
def http_session_request(
    session: str,
    url: str,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    cookies: Optional[dict[str, str]] = None,
    body: Optional[str] = None,
    auth: Optional[str] = None,
    follow_redirects: bool = True,
    max_body: int = 5000,
    timeout: int = 30,
) -> str:
    """有状态 HTTP 请求（Cookie/鉴权跨调用复用，状态落盘工作区）。"""
    if not session or not str(session).strip():
        return "(错误: 必须提供 session 会话名)"
    session = str(session).strip()
    try:
        with _lock:
            store, path = _load_store()
            sess = _get_session(store, session)
            merged_headers = {**sess.get("headers", {}), **(headers or {})}
            merged_cookies = {**sess.get("cookies", {}), **(cookies or {})}
            auth_blob = auth if auth is not None else sess.get("auth", "")
            auth_arg = _parse_auth(auth_blob)
            try:
                resp, final_url, final_method, hops, cookie_jar = _perform_request(
                    url=url,
                    method=method,
                    headers=merged_headers,
                    cookies=merged_cookies,
                    auth=auth_arg,
                    content=body,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                )
            except ValueError as exc:
                # SSRF 拦截等：直接返回可读错误，不污染会话
                return f"(请求被拦截: {exc})"
            sess["cookies"] = cookie_jar
            sess["updated_at"] = _now()
            sess["request_count"] = int(sess.get("request_count", 0)) + 1
            sess["last_url"] = final_url
            _save_store(store, path)
        return _render_response(
            session_name=session,
            sess=sess,
            resp=resp,
            final_url=final_url,
            final_method=final_method,
            hops=hops,
            max_body=max_body,
        )
    except Exception as exc:  # noqa: BLE001
        return f"(请求失败: {type(exc).__name__}: {exc})"


# ---------------------------------------------------------------------------
# 工具 2：配置会话（默认头 / Cookie / 鉴权）
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "配置有状态会话的默认请求头 / Cookie / Basic 鉴权，后续 "
        "http_session_request 自动携带。常用于把登录得到的 Token 写入会话头"
        "（如 Authorization: Bearer xxx），或预置固定 Cookie。"
        "参数: session 会话名; headers 合并写入的默认请求头(可选); "
        "cookies 合并写入的 Cookie(可选); auth 'user:pass' 设置 Basic 鉴权"
        "(可选, 传空字符串则清除已存鉴权)。"
    ),
    category="security",
    tags=["web", "http", "session", "auth", "cookie"],
)
def http_session_set(
    session: str,
    headers: Optional[dict[str, str]] = None,
    cookies: Optional[dict[str, str]] = None,
    auth: Optional[str] = None,
) -> str:
    """设置会话的持久请求头 / Cookie / 鉴权。"""
    if not session or not str(session).strip():
        return "(错误: 必须提供 session 会话名)"
    session = str(session).strip()
    with _lock:
        store, path = _load_store()
        sess = _get_session(store, session)
        if headers:
            sess["headers"].update({str(k): str(v) for k, v in headers.items()})
        if cookies:
            sess["cookies"].update({str(k): str(v) for k, v in cookies.items()})
        if auth is not None:
            sess["auth"] = str(auth)
        sess["updated_at"] = _now()
        _save_store(store, path)
        return (
            f"[会话 {session} 已更新] headers={list(sess['headers'].keys())} "
            f"cookies={list(sess['cookies'].keys())} "
            f"auth={'已设置' if sess.get('auth') else '无'}"
        )


# ---------------------------------------------------------------------------
# 工具 3：查看会话
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "列出当前工作区中已保存的有状态 HTTP 会话及状态"
        "（会话名、Cookie、默认头、鉴权、请求次数、最近 URL）。"
        "无参数。会话续跑后可用它恢复对登录态的认知。"
    ),
    category="security",
    tags=["web", "http", "session", "inspect"],
)
def http_session_list() -> str:
    """列出全部有状态会话及其 Cookie/头/鉴权状态。"""
    with _lock:
        store, _ = _load_store()
    if not store:
        return "(当前无已保存的 HTTP 会话)"
    lines: list[str] = []
    for name, sess in store.items():
        if not isinstance(sess, dict):
            continue
        ck = sess.get("cookies", {}) or {}
        hd = sess.get("headers", {}) or {}
        ck_str = ", ".join(f"{k}={v}" for k, v in ck.items()) or "(无)"
        hd_str = ", ".join(hd.keys()) or "(无)"
        lines.append(
            f"- {name}: cookies=[{ck_str}] headers=[{hd_str}] "
            f"auth={'有' if sess.get('auth') else '无'} "
            f"requests={sess.get('request_count', 0)} "
            f"last={sess.get('last_url') or '(无)'} "
            f"updated={sess.get('updated_at') or '?'}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 工具 4：清除会话
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "清除有状态 HTTP 会话：指定 session 名清除该会话，留空则清除全部。"
        "参数: session 会话名(可选, 留空清除全部)。"
    ),
    category="security",
    tags=["web", "http", "session", "clear"],
)
def http_session_clear(session: str = "") -> str:
    """清除指定会话（留空清除全部）。"""
    with _lock:
        store, path = _load_store()
        if session and str(session).strip():
            name = str(session).strip()
            existed = store.pop(name, None) is not None
            _save_store(store, path)
            return f"[会话 {name} 已清除]" if existed else f"(会话 {name} 不存在)"
        n = len(store)
        store.clear()
        _save_store(store, path)
        return f"[已清除全部 {n} 个会话]"


# ---------------------------------------------------------------------------
# 工具 5：证据复核（二次验证纪律）
# ---------------------------------------------------------------------------

@register_tool(
    description=(
        "复核一条已声明结论的证据：重新发起请求，按给定判据判断证据是否复现。"
        "用于「漏洞证据链二次验证」——在把结论写进报告前，重放一次证据请求，"
        "确认它不是误报。参数: url 证据请求 URL; method 方法(默认 GET); "
        "headers 请求头(可选); body 请求体(可选); session 复用哪个有状态会话的"
        "登录态(可选, 取证需要认证的接口时填, 与 http_session_request 同一会话名); "
        "expect_status 期望状态码(如 200, 可选); expect_contains 响应体应包含的"
        "关键串(如 'root:' / 'uid=0', 可选); follow_redirects 是否跟随重定向(默认是)。"
        "返回「复现/未复现/无法判定」+ 实测状态码 + 响应片段；是否降级/保留该结论"
        "永远由你决定，本工具只给出复核事实。"
    ),
    category="security",
    tags=["web", "http", "verify", "evidence", "session"],
)
def verify_evidence(
    url: str,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    body: Optional[str] = None,
    session: str = "",
    expect_status: int = 0,
    expect_contains: str = "",
    follow_redirects: bool = True,
    max_body: int = 3000,
    timeout: int = 30,
) -> str:
    """重放证据请求，按判据给出复现/未复现结论（不改变任何结论定级）。"""
    session = (session or "").strip()
    try:
        with _lock:
            store, path = _load_store()
            merged_headers: dict[str, str] = dict(headers or {})
            merged_cookies: dict[str, str] = {}
            if session:
                sess = _get_session(store, session)
                merged_headers = {**(sess.get("headers", {}) or {}), **merged_headers}
                merged_cookies = dict(sess.get("cookies", {}) or {})
            auth_arg = _parse_auth(
                (_get_session(store, session).get("auth", "") if session else "")
            )
            resp, final_url, final_method, hops, cookie_jar = _perform_request(
                url=url,
                method=method,
                headers=merged_headers,
                cookies=merged_cookies,
                auth=auth_arg,
                content=body,
                follow_redirects=follow_redirects,
                timeout=timeout,
            )
            if session:
                sess = _get_session(store, session)
                sess["cookies"] = cookie_jar
                sess["updated_at"] = _now()
                _save_store(store, path)
    except ValueError as exc:
        return f"[复核结果] ⚠️ 无法判定（请求被拦截: {exc}）"
    except Exception as exc:  # noqa: BLE001
        return f"[复核结果] ⚠️ 无法判定（请求失败: {type(exc).__name__}: {exc}）"

    status = resp.status_code if resp is not None else 0
    text = resp.text if resp is not None else ""
    criteria: list[str] = []
    ok = True
    if expect_status:
        criteria.append(f"status=={expect_status}")
        ok = ok and status == expect_status
    if expect_contains:
        criteria.append(f"body⊇'{expect_contains}'")
        ok = ok and expect_contains in text
    if not criteria:
        verdict = "⚠️ 无法判定（未提供 expect_status / expect_contains 判据）"
    else:
        verdict = "✅ 复现" if ok else "❌ 未复现"

    lines = [
        f"[复核结果] {verdict}",
        f"{final_method} {final_url}",
        f"实测: status={status}"
        + (f" | 命中判据: {'; '.join(criteria)}" if criteria else ""),
    ]
    if hops:
        lines.append("[重定向] " + " ; ".join(hops))
    snippet = text[:max_body] if max_body and max_body > 0 else text
    lines.append("--- 响应片段 ---")
    lines.append(snippet)
    return "\n".join(lines)


__all__ = [
    "http_session_request",
    "http_session_set",
    "http_session_list",
    "http_session_clear",
    "verify_evidence",
]