"""工具缺失自洽修复：检测到系统二进制缺失时，自动安装并重试。

设计目标:
- 工具执行报 ``command not found`` 时，不再只返回提示文字，
  而是查映射表 → 走沙箱审批 → 执行安装命令 → 重试原工具调用。
- 覆盖 apt（Debian/Ubuntu）、winget/choco（Windows）、pip（Python 包）。
- 幂等：已安装的工具不重复安装；安装失败返回明确原因。

与沙箱的关系:
- 自动安装属 ``generic_linux_command`` 危险区工具，走沙箱审批流程。
- 安装成功后发放会话级审批令牌，后续相同工具安装不再打扰用户。
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Optional

from baize.executors import build_executor

logger = logging.getLogger("baize.tools.auto_install")


# ---------------------------------------------------------------------------
# 包管理器检测
# ---------------------------------------------------------------------------

def detect_package_manager() -> Optional[str]:
    """检测当前系统可用的包管理器。

    Windows: winget > choco
    Linux: apt > dnf > pacman
    macOS: brew
    """
    for pm in ("winget", "choco", "apt", "dnf", "brew", "pacman"):
        if shutil.which(pm):
            return pm
    return None


# ---------------------------------------------------------------------------
# 工具名 → 安装命令映射（按包管理器）
# ---------------------------------------------------------------------------

# 二进制名 → 各包管理器下的安装命令（None 表示该管理器不支持）
INSTALL_COMMANDS: dict[str, dict[str, Optional[str]]] = {
    # 基础
    "curl": {"apt": "curl", "winget": "curl", "choco": "curl", "pip": None},
    "wget": {"apt": "wget", "winget": "wget", "choco": "wget", "pip": None},
    "git": {"apt": "git", "winget": "Git.Git", "choco": "git", "pip": None},
    "jq": {"apt": "jq", "winget": "jqlang.jq", "choco": "jq", "pip": None},
    "unzip": {"apt": "unzip", "winget": None, "choco": "unzip", "pip": None},
    "openssl": {"apt": "openssl", "winget": "ShiningLight.OpenSSL", "choco": "openssl", "pip": None},
    "ssh": {"apt": "openssh-client", "winget": None, "choco": "openssh", "pip": None},
    "nc": {"apt": "netcat-openbsd", "winget": None, "choco": "nmap", "pip": None},
    "dig": {"apt": "bind9-dnsutils", "winget": None, "choco": "bind-toolsonly", "pip": None},
    "whois": {"apt": "whois", "winget": None, "choco": "whois", "pip": None},
    "traceroute": {"apt": "traceroute", "winget": None, "choco": "traceroute", "pip": None},
    "bwrap": {"apt": "bubblewrap", "winget": None, "choco": None, "pip": None},
    # recon / web / password / forensic / wireless
    "nmap": {"apt": "nmap", "winget": "Insecure.Nmap", "choco": "nmap", "pip": None},
    "masscan": {"apt": "masscan", "winget": None, "choco": "masscan", "pip": None},
    "arp-scan": {"apt": "arp-scan", "winget": None, "choco": None, "pip": None},
    "tshark": {"apt": "tshark", "winget": "WiresharkFoundation.Wireshark", "choco": "wireshark", "pip": None},
    "sqlmap": {"apt": "sqlmap", "winget": None, "choco": "sqlmap", "pip": "sqlmap"},
    "nikto": {"apt": "nikto", "winget": None, "choco": "nikto", "pip": None},
    "hydra": {"apt": "hydra", "winget": None, "choco": "hydra", "pip": None},
    "john": {"apt": "john", "winget": None, "choco": "john", "pip": None},
    "hashcat": {"apt": "hashcat", "winget": None, "choco": "hashcat", "pip": None},
    "hashid": {"apt": "hashid", "winget": None, "choco": None, "pip": "hashid"},
    "strings": {"apt": "binutils", "winget": None, "choco": None, "pip": None},
    "exiftool": {"apt": "libimage-exiftool-perl", "winget": "OliverBetz.ExifTool", "choco": "exiftool", "pip": None},
    "binwalk": {"apt": "binwalk", "winget": None, "choco": None, "pip": "binwalk"},
    "aircrack-ng": {"apt": "aircrack-ng", "winget": None, "choco": None, "pip": None},
    "nuclei": {"apt": "nuclei", "winget": "ProjectDiscovery.Nuclei", "choco": None, "pip": None},
    "httpx": {"apt": "httpx-toolkit", "winget": None, "choco": None, "pip": None},
    "gobuster": {"apt": "gobuster", "winget": None, "choco": None, "pip": None},
    "ffuf": {"apt": "ffuf", "winget": None, "choco": None, "pip": None},
    "wafw00f": {"apt": None, "winget": None, "choco": None, "pip": "wafw00f"},
    "msfconsole": {"apt": "metasploit-framework", "winget": None, "choco": "metasploit", "pip": None},
    "searchsploit": {"apt": "exploitdb", "winget": None, "choco": None, "pip": None},
    "whatweb": {"apt": "whatweb", "winget": None, "choco": None, "pip": None},
}


def _build_install_command(binary: str, pm: str) -> Optional[str]:
    """构造安装命令字符串。"""
    pkg = INSTALL_COMMANDS.get(binary, {}).get(pm)
    if pkg is None:
        return None
    if pm == "apt":
        return f"apt-get install -y {pkg}"
    if pm == "winget":
        return f"winget install --id {pkg} -e --accept-source-agreements --accept-package-agreements"
    if pm == "choco":
        return f"choco install {pkg} -y"
    if pm == "dnf":
        return f"dnf install -y {pkg}"
    if pm == "pacman":
        return f"pacman -S --noconfirm {pkg}"
    if pm == "brew":
        return f"brew install {pkg}"
    if pm == "pip":
        return f"pip install {pkg}"
    return None


def _needs_sudo(pm: str) -> bool:
    """apt/dnf/pacman 安装系统包需要 root 权限。"""
    return pm in ("apt", "dnf", "pacman")


# ---------------------------------------------------------------------------
# 自动安装入口
# ---------------------------------------------------------------------------

async def try_auto_install(
    binary: str,
    sandbox: Optional[object] = None,
    session_id: str = "",
    executor: Optional[object] = None,
) -> tuple[bool, str]:
    """检测到二进制缺失时，尝试自动安装。

    Args:
        binary: 缺失的系统二进制名（如 ``nmap`` / ``sqlmap``）。
        sandbox: 沙箱控制器（``baize.sandbox.Sandbox``）；为 None 时跳过审批。
        session_id: 会话 ID（用于沙箱审批状态隔离）。
        executor: 执行器实例；为 None 时默认构建。

    Returns:
        (success, message): 安装成功返回 (True, 输出)；失败返回 (False, 原因)。
    """
    pm = detect_package_manager()
    if pm is None:
        return False, "未检测到可用的包管理器（apt/winget/choco/dnf/brew/pacman）"

    cmd = _build_install_command(binary, pm)
    if cmd is None:
        return False, f"工具 {binary} 在 {pm} 下无对应安装命令，请手动安装"

    # 走沙箱审批（自动安装属危险区 generic_linux_command）
    if sandbox is not None and session_id:
        check_result = sandbox.check("generic_linux_command", session_id)
        if check_result.get("level") == "approve":
            try:
                approved = await sandbox.request_approval(
                    "generic_linux_command", session_id, timeout=300.0
                )
            except Exception as exc:  # noqa: BLE001
                return False, f"沙箱审批异常: {exc}"
            if not approved:
                return False, f"用户拒绝安装 {binary}（{pm} {cmd}）"
        elif check_result.get("level") == "deny":
            return False, f"沙箱策略禁止执行安装命令: {check_result.get('reason')}"

    # 需要 sudo 的包管理器：用 sudo -n（非交互）尝试，失败提示需要 root
    # Windows 无 geteuid，跳过
    if _needs_sudo(pm) and hasattr(os, "geteuid") and os.geteuid() != 0:
        cmd = f"sudo -n {cmd}"

    logger.info("自动安装工具: %s → %s", binary, cmd)
    ex = executor or build_executor()
    try:
        result = await ex.run(cmd, timeout=300)
    except Exception as exc:  # noqa: BLE001
        return False, f"安装命令执行异常: {type(exc).__name__}: {exc}"

    # 记录沙箱执行结果（发放令牌）
    if sandbox is not None and session_id:
        sandbox.record_execution("generic_linux_command", session_id, success=(result.returncode == 0))

    if result.returncode != 0:
        return False, f"安装失败 (exit={result.returncode}): {result.text[:500]}"

    # 验证二进制是否真的可用
    if shutil.which(binary):
        logger.info("工具 %s 安装成功，二进制已就绪", binary)
        return True, f"工具 {binary} 安装成功"
    return True, f"工具 {binary} 安装命令已执行，但二进制仍未在 PATH 中检测到（可能需要重开终端/刷新 PATH）"


__all__ = [
    "detect_package_manager",
    "try_auto_install",
    "INSTALL_COMMANDS",
]
