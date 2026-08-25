"""沙箱边界系统（参考 Apache Maka 的 sandbox boundary）。

在现有 guardrails 基础上，增加显式的沙箱边界概念：
- 将工具分为「安全区」和「危险区」两类。
- 危险区工具执行前需要审批（approval-gated）。
- 支持三级权限：允许 / 审批后允许 / 禁止。
- 可恢复的权限边界：任务中断后恢复时保留审批状态。

与 guardrails 的关系：
- guardrails 负责输入/输出过滤与 SSRF 防护。
- sandbox 负责工具执行权限的边界控制。
- 两者互补，sandbox 调用 guardrails 做 URL/IP 检查。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("baize.sandbox")

# ---- 工具分类 ----------------------------------------------------------------

# 安全区工具：不需要审批，可直接执行
SAFE_TOOLS: set[str] = {
    # 只读类
    "shared_browser_snapshot",
    "shared_browser_status",
    "shared_browser_evaluate",
    "make_web_search_with_explanation",
    "make_google_search",
    "shodan_search",
    "read_file",
    "think",
}

# 危险区工具：需要审批才能执行
DANGEROUS_TOOLS: set[str] = {
    "generic_linux_command",
    "execute_code",
    "http_request",
    "web_request_framework",
    "port_scan",
    "shared_browser_open",
    "shared_browser_click",
    "shared_browser_fill",
    "shared_browser_close",
    "deploy_payload",
    "exploit",
    "run_metasploit",
}

# 需人工确认的工具（即使审批通过也需要用户确认）
CONFIRM_TOOLS: set[str] = {
    "shared_browser_open",
    "shared_browser_wait_user",
}


# ---- 权限级别 ----------------------------------------------------------------

class PermissionLevel:
    ALLOW = "allow"           # 直接允许
    APPROVE = "approve"       # 需要审批
    DENY = "deny"             # 禁止
    CONFIRM = "confirm"       # 需要用户确认


@dataclass
class SandboxPolicy:
    """沙箱策略配置。"""

    enabled: bool = True
    """是否启用沙箱边界。"""

    default_permission: str = PermissionLevel.APPROVE
    """未分类工具的默认权限。"""

    tool_permissions: dict[str, str] = field(default_factory=dict)
    """工具 → 权限级别 映射。"""

    auto_approve_after: int = 3
    """同一工具在同一会话中连续审批通过后，自动允许后续调用。"""

    max_dangerous_per_turn: int = 10
    """单轮对话中危险工具的最大调用次数。"""


class Sandbox:
    """沙箱边界控制器。

    用法::

        sandbox = Sandbox(SandboxPolicy())
        result = sandbox.check("generic_linux_command", session_id="sess_1")
        if result.level == PermissionLevel.APPROVE:
            sandbox.approve("generic_linux_command", "sess_1")
        # ... 执行工具 ...
        sandbox.record_execution("generic_linux_command", "sess_1", success=True)
    """

    def __init__(self, policy: Optional[SandboxPolicy] = None, data_dir: Optional[Path] = None) -> None:
        self.policy = policy or SandboxPolicy()
        if data_dir is None:
            import os as _os
            data_dir = Path(_os.environ.get("BAIZE_DATA_DIR", str(Path.home() / ".baize"))) / "sandbox"
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # 会话级审批状态
        self._approvals: dict[str, dict[str, int]] = {}  # session_id -> {tool_name: approved_count}
        self._denials: dict[str, set[str]] = {}           # session_id -> {tool_name}
        # 一次性审批令牌
        self._tokens: dict[str, set[str]] = {}            # session_id -> {tool_name}
        # 异步审批等待：agent 暂停等待用户审批
        self._pending_approvals: dict[str, dict[str, asyncio.Future]] = {}  # session_id -> {tool_name: Future[bool]}

    def _load_session_state(self, session_id: str) -> None:
        path = self._data_dir / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._approvals[session_id] = data.get("approvals", {})
                self._denials[session_id] = set(data.get("denials", []))
            except (json.JSONDecodeError, OSError):
                pass

    def _save_session_state(self, session_id: str) -> None:
        path = self._data_dir / f"{session_id}.json"
        data = {
            "approvals": self._approvals.get(session_id, {}),
            "denials": list(self._denials.get(session_id, set())),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 权限检查 ------------------------------------------------------------

    def classify(self, tool_name: str) -> str:
        """返回工具的默认分类：safe / dangerous / confirm。"""
        if tool_name in CONFIRM_TOOLS:
            return "confirm"
        if tool_name in SAFE_TOOLS:
            return "safe"
        if tool_name in DANGEROUS_TOOLS:
            return "dangerous"
        # 未知工具 → 看配置
        perm = self.policy.tool_permissions.get(tool_name)
        if perm == PermissionLevel.ALLOW:
            return "safe"
        return "dangerous"

    def check(self, tool_name: str, session_id: str) -> dict:
        """检查工具是否允许执行。

        返回: {"allowed": bool, "level": str, "reason": str}
        """
        if not self.policy.enabled:
            return {"allowed": True, "level": PermissionLevel.ALLOW, "reason": "沙箱已关闭"}

        with self._lock:
            self._load_session_state(session_id)

            # 1. 检查硬拒绝
            if tool_name in self._denials.get(session_id, set()):
                return {"allowed": False, "level": PermissionLevel.DENY, "reason": "该工具已被拒绝"}

            # 2. 检查一次性令牌
            if tool_name in self._tokens.get(session_id, set()):
                return {"allowed": True, "level": PermissionLevel.ALLOW, "reason": "持有审批令牌"}

            # 3. 检查配置中的权限
            perm = self.policy.tool_permissions.get(tool_name)
            if perm == PermissionLevel.ALLOW:
                return {"allowed": True, "level": PermissionLevel.ALLOW, "reason": "配置允许"}
            if perm == PermissionLevel.DENY:
                return {"allowed": False, "level": PermissionLevel.DENY, "reason": "配置禁止"}

            # 4. 安全区工具直接允许
            if tool_name in SAFE_TOOLS:
                return {"allowed": True, "level": PermissionLevel.ALLOW, "reason": "安全区工具"}

            # 5. 自动审批：同一工具已连续通过多次
            approved = self._approvals.get(session_id, {}).get(tool_name, 0)
            if approved >= self.policy.auto_approve_after:
                return {"allowed": True, "level": PermissionLevel.ALLOW, "reason": f"已自动审批（{approved}次）"}

            # 6. 需要审批
            return {"allowed": False, "level": PermissionLevel.APPROVE, "reason": "需要审批"}

    def approve(self, tool_name: str, session_id: str) -> None:
        """审批通过某工具（一次性令牌，执行后失效）。"""
        with self._lock:
            self._tokens.setdefault(session_id, set()).add(tool_name)
        # 同时解析等待中的审批 Future
        self._resolve_approval(tool_name, session_id, True)

    def deny(self, tool_name: str, session_id: str) -> None:
        """永久拒绝某工具。"""
        with self._lock:
            self._denials.setdefault(session_id, set()).add(tool_name)
            self._save_session_state(session_id)
        # 同时解析等待中的审批 Future
        self._resolve_approval(tool_name, session_id, False)

    # ---- 异步审批等待 ---------------------------------------------------------

    async def request_approval(self, tool_name: str, session_id: str, timeout: float = 300.0) -> bool:
        """等待用户审批。返回 True 表示批准，False 表示拒绝或超时。"""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        with self._lock:
            self._pending_approvals.setdefault(session_id, {})[tool_name] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            with self._lock:
                self._pending_approvals.get(session_id, {}).pop(tool_name, None)
            return False

    def _resolve_approval(self, tool_name: str, session_id: str, approved: bool) -> None:
        """解析等待中的审批请求。"""
        with self._lock:
            fut = self._pending_approvals.get(session_id, {}).pop(tool_name, None)
        if fut is not None and not fut.done():
            fut.set_result(approved)

    def record_execution(self, tool_name: str, session_id: str, success: bool) -> None:
        """记录工具执行结果，更新审批计数。"""
        with self._lock:
            # 消耗令牌
            self._tokens.get(session_id, set()).discard(tool_name)
            if success:
                self._approvals.setdefault(session_id, {})
                self._approvals[session_id][tool_name] = self._approvals[session_id].get(tool_name, 0) + 1
            self._save_session_state(session_id)

    def reset_session(self, session_id: str) -> None:
        """重置会话的审批状态。"""
        with self._lock:
            self._approvals.pop(session_id, None)
            self._denials.pop(session_id, None)
            self._tokens.pop(session_id, None)
            path = self._data_dir / f"{session_id}.json"
            if path.exists():
                path.unlink()

    def get_session_stats(self, session_id: str) -> dict:
        """获取会话的沙箱统计。"""
        with self._lock:
            self._load_session_state(session_id)
            return {
                "approvals": dict(self._approvals.get(session_id, {})),
                "denials": list(self._denials.get(session_id, set())),
                "active_tokens": list(self._tokens.get(session_id, set())),
            }