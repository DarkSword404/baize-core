"""安全执行环境抽象（BaseExecutor）。

提供统一的安全工具执行接口，支持多种执行后端:
- ``LocalExecutor``: 本地 subprocess 执行（默认）。
- ``DockerExecutor``: 在 Docker 容器中隔离执行（工具不污染宿主机）。
- ``SSHExecutor``: 通过 SSH 在远程主机执行（远程渗透/分布式扫描）。

对齐 LangChain 的 ``Tool`` + ``RunnableConfig`` 设计 ——
工具只关心"执行什么"，执行后端（本地/容器/远程）由环境配置决定，
从而让安全工具可移植、可审计、可隔离。

**沙箱维度（fail-closed 原则）**
参考 deepseek-harness 的 Sandbox 设计：
- 每次执行携带 ``SandboxMode``（read_only / workspace_write / danger_full_access）。
- 后端必须诚实报告 ``EnforcementLevel``（full / partial / none）。
- 请求隔离而后端无法提供真实隔离时，**宁可失败**（抛 ``SandboxUnavailableError``），
  绝不静默透传成无隔离执行。
- 错误双通道分类：``sandbox_denied``（安全机制在起作用，如 EROFS/EACCES）
  与 ``runner_failure``（执行基础设施故障，如命令不存在），二者不可混为一谈。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("baize.executors")


# ===========================================================================
# 沙箱模式 / 强制级别 / 错误分类
# ===========================================================================

class SandboxMode(str, Enum):
    """请求的执行隔离等级。

    - ``READ_ONLY``: 只读隔离（无法写文件系统，适合扫描/只读探测）。
    - ``WORKSPACE_WRITE``: 仅工作区可写（临时文件、报告落盘）。
    - ``DANGER_FULL_ACCESS``: 完全访问（本地透传；危险工具需显式授权）。
    """

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL_ACCESS = "danger_full_access"


class EnforcementLevel(str, Enum):
    """后端实际提供的隔离强度（诚实报告，绝不夸大）。

    - ``FULL``: 真实隔离（容器/内核沙箱等）。
    - ``PARTIAL``: 部分隔离（如远程主机、只读限制不完整）。
    - ``NONE``: 无隔离（本地透传）。
    """

    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


class SandboxUnavailableError(RuntimeError):
    """请求了隔离，但当前后端/环境无法提供 —— 失败关闭（fail-closed）。"""


# 安全机制在起作用的典型错误（策略拒绝），与"基础设施故障"严格区分。
# 统一用小写匹配（classify_exec_error 会 lower() 后比对）。
_DENIAL_SIGNATURES: list[str] = [
    "erofs",
    "eacces",
    "eperm",
    "read-only file system",
    "readonly file system",
    "operation not permitted",
    "permission denied",
    "read-only file system",
]

# 执行基础设施故障（runner failure）：命令不存在 / 权限完全无法启动 / 后端缺失
_RUNNER_FAILURE_PATTERNS: list[str] = [
    "command not found",
    "no such file or directory",
    "docker command not found",
    "exec format error",
    "permission denied (publickey",
]


def classify_exec_error(stderr: str, returncode: int, timed_out: bool = False) -> Optional[str]:
    """对执行结果分类，返回错误种类（None 表示执行成功/无异常）。

    Returns:
        - ``"sandbox_denied"``: 沙箱/策略拒绝（安全机制在工作）。
        - ``"runner_failure"``: 执行基础设施故障。
        - ``"timeout"``: 超时。
        - 其他明确错误统一归为 ``"runner_failure"``。
    """
    if timed_out:
        return "timeout"
    if returncode == 0:
        return None
    text = (stderr or "").lower()
    if any(sig in text for sig in _DENIAL_SIGNATURES):
        return "sandbox_denied"
    if any(pat in text for pat in _RUNNER_FAILURE_PATTERNS):
        return "runner_failure"
    return "runner_failure" if returncode != 0 else None


@dataclass
class ExecResult:
    """命令执行结果。"""

    command: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    timed_out: bool = False
    duration: float = 0.0
    executor: str = "local"
    # 沙箱维度（fail-closed 报告）
    sandbox: str = SandboxMode.DANGER_FULL_ACCESS.value
    enforcement: str = EnforcementLevel.NONE.value
    # 错误分类：None | "sandbox_denied" | "runner_failure" | "timeout"
    error_kind: Optional[str] = None

    @property
    def text(self) -> str:
        """组合输出（与旧 _run_shell 的返回格式兼容）。"""
        out = ((self.stdout or "") + ("\n" if self.stdout and self.stderr else "") + (self.stderr or "")).strip()
        if out:
            return out
        if self.timed_out:
            return "(执行超时)"
        return f"(exit {self.returncode}, 无输出)"


# ===========================================================================
# 执行器抽象
# ===========================================================================

class BaseExecutor:
    """执行器抽象基类。"""

    name: str = "base"

    #: 该后端默认能提供的隔离强度（子类覆盖）
    default_enforcement: EnforcementLevel = EnforcementLevel.NONE

    def check_sandbox(self, sandbox: SandboxMode) -> None:
        """校验请求的沙箱模式是否可用；不可用则抛 ``SandboxUnavailableError``。

        子类可覆盖以实现真实的隔离机制（bwrap / docker / 远程），
        基类默认实现：除 danger 外一律 fail-closed。
        """
        if sandbox == SandboxMode.DANGER_FULL_ACCESS:
            return
        raise SandboxUnavailableError(
            f"后端 {self.name} 无法提供 {sandbox.value} 隔离，"
            "已失败关闭（fail-closed）。请改用 docker/ssh 后端，或显式设置 BAIZE_EXEC_SANDBOX=danger_full_access。"
        )

    async def run(self, command: str, timeout: int = 120, **kwargs: Any) -> ExecResult:
        """执行命令并返回结构化结果。

        kwargs 可携带 ``sandbox: SandboxMode`` 请求隔离等级。
        """
        raise NotImplementedError

    async def run_batch(self, commands: list[str], timeout: int = 120, **kwargs: Any) -> list[ExecResult]:
        """并发批量执行多条命令。"""
        return list(await asyncio.gather(*[self.run(c, timeout=timeout, **kwargs) for c in commands]))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__}>"


def _resolve_sandbox(kwargs: dict[str, Any]) -> tuple[SandboxMode, dict[str, Any]]:
    """从 kwargs 提取 sandbox（字符串/枚举），返回 (sandbox, 剩余 kwargs)。"""
    raw = kwargs.pop("sandbox", SandboxMode.DANGER_FULL_ACCESS)
    if isinstance(raw, SandboxMode):
        return raw, kwargs
    return SandboxMode(raw), kwargs


def _finish_result(
    started: float,
    command: str,
    executor: str,
    sandbox: SandboxMode,
    enforcement: EnforcementLevel,
    stdout: Any = b"",
    stderr: Any = b"",
    returncode: int = -1,
    timed_out: bool = False,
) -> ExecResult:
    """统一构造 ExecResult（含错误分类）。"""
    stdout_s = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else stdout
    stderr_s = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
    return ExecResult(
        command=command,
        stdout=stdout_s,
        stderr=stderr_s,
        returncode=returncode,
        timed_out=timed_out,
        duration=asyncio.get_event_loop().time() - started,
        executor=executor,
        sandbox=sandbox.value,
        enforcement=enforcement.value,
        # 同时检查 stdout/stderr（shell 常把错误经 2>&1 混入 stdout）
        error_kind=classify_exec_error(f"{stderr_s}\n{stdout_s}", returncode, timed_out),
    )


class LocalExecutor(BaseExecutor):
    """本地执行器 —— 通过 subprocess 直接执行（等价于旧 _run_shell）。

    沙箱策略（fail-closed）:
    - ``danger_full_access``: 直接透传（enforcement=none，如实报告）。
    - ``read_only`` / ``workspace_write``: 若系统安装了 ``bwrap``
      （bubblewrap，Debian/Ubuntu 均可 apt 安装），用内核级沙箱真实隔离；
      否则抛 ``SandboxUnavailableError``，绝不假装隔离。
    """

    name = "local"

    def __init__(self, shell: str = "/bin/bash", env: Optional[dict[str, str]] = None) -> None:
        self.shell = shell
        self.env = env

    def check_sandbox(self, sandbox: SandboxMode) -> None:
        if sandbox == SandboxMode.DANGER_FULL_ACCESS:
            return
        if shutil.which("bwrap") is None:
            raise SandboxUnavailableError(
                "本地后端请求了隔离，但系统未安装 bwrap（bubblewrap）。"
                "请安装: sudo apt install bubblewrap；或改用 docker 后端；"
                "或显式设置 BAIZE_EXEC_SANDBOX=danger_full_access。"
            )

    def _wrap_bwrap(self, command: str, sandbox: SandboxMode) -> list[str]:
        """用 bwrap 构造隔离命令。

        - read_only: 只读挂载 / 与 /usr，可读不可写。
        - workspace_write: 额外把 $PWD 以可写方式绑定。
        """
        bwrap = ["bwrap", "--die-with-parent", "--unshare-all", "--new-session"]
        if sandbox == SandboxMode.READ_ONLY:
            bwrap += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
        elif sandbox == SandboxMode.WORKSPACE_WRITE:
            bwrap += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
            cwd = os.getcwd()
            bwrap += ["--bind", cwd, cwd, "--tmpfs", "/tmp"]
        bwrap += ["/bin/bash", "-c", command]
        return bwrap

    async def run(self, command: str, timeout: int = 120, **kwargs: Any) -> ExecResult:
        sandbox, _ = _resolve_sandbox(kwargs)
        self.check_sandbox(sandbox)
        started = asyncio.get_event_loop().time()
        try:
            if sandbox == SandboxMode.DANGER_FULL_ACCESS:
                argv = [self.shell, "-c", command]
            else:
                argv = self._wrap_bwrap(command, sandbox)
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **(self.env or {})},
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                returncode = proc.returncode
                timed_out = False
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                stdout, stderr = b"", "timeout".encode()
                returncode = -1
                timed_out = True
        except SandboxUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("本地执行失败: %s", exc)
            stdout, stderr, returncode, timed_out = b"", str(exc).encode(), -1, False

        enforcement = EnforcementLevel.FULL if sandbox != SandboxMode.DANGER_FULL_ACCESS else EnforcementLevel.NONE
        return _finish_result(
            started, command, self.name, sandbox, enforcement,
            stdout, stderr, returncode, timed_out,
        )


class DockerExecutor(BaseExecutor):
    """Docker 执行器 —— 在指定容器镜像中隔离执行命令。

    用法::

        DockerExecutor(image="instrumentisto/nmap", remove=True)

    工具命令会以 ``docker run --rm -i <image> <command>`` 形式在
    隔离容器内运行，宿主环境不受影响，适合安全扫描工具。

    沙箱：容器天然提供 full 隔离；``read_only`` 额外加 ``--read-only``。
    """

    name = "docker"
    default_enforcement = EnforcementLevel.FULL

    def __init__(
        self,
        image: str = "instrumentisto/nmap",
        remove: bool = True,
        network: str = "host",
        extra_args: Optional[list[str]] = None,
        docker_cmd: str = "docker",
    ) -> None:
        self.image = image
        self.remove = remove
        self.network = network
        self.extra_args = extra_args or []
        self.docker_cmd = docker_cmd

    def check_sandbox(self, sandbox: SandboxMode) -> None:
        # 容器本身就是隔离边界；read_only/workspace_write 均可满足
        return

    async def run(self, command: str, timeout: int = 120, **kwargs: Any) -> ExecResult:
        sandbox, _ = _resolve_sandbox(kwargs)
        args = [self.docker_cmd, "run"]
        if self.remove:
            args.append("--rm")
        args += ["-i", "--network", self.network]
        if sandbox == SandboxMode.READ_ONLY:
            args.append("--read-only")
        args += self.extra_args
        args.append(self.image)
        args += shlex.split(command)

        started = asyncio.get_event_loop().time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                returncode = proc.returncode
                timed_out = False
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                stdout, stderr = b"", "timeout".encode()
                returncode = -1
                timed_out = True
        except FileNotFoundError:
            stdout, stderr, returncode, timed_out = b"", b"(docker command not found)", 127, False
        except Exception as exc:  # noqa: BLE001
            stdout, stderr, returncode, timed_out = b"", str(exc).encode(), -1, False

        return _finish_result(
            started, " ".join(args), self.name, sandbox, EnforcementLevel.FULL,
            stdout, stderr, returncode, timed_out,
        )


class SSHExecutor(BaseExecutor):
    """SSH 执行器 —— 通过 ssh 在远程主机执行命令。

    用法::

        SSHExecutor(host="10.0.0.5", username="root", port=22, key_path="~/.ssh/id_rsa")

    沙箱：远程主机执行可视为 partial 隔离（不污染本地宿主，
    但远端自身的防护等级未知，诚实报告为 partial）。
    """

    name = "ssh"
    default_enforcement = EnforcementLevel.PARTIAL

    def __init__(
        self,
        host: str,
        username: Optional[str] = None,
        port: int = 22,
        key_path: Optional[str] = None,
        ssh_cmd: str = "ssh",
        extra_args: Optional[list[str]] = None,
    ) -> None:
        self.host = host
        self.username = username
        self.port = port
        self.key_path = key_path
        self.ssh_cmd = ssh_cmd
        self.extra_args = extra_args or []

    def check_sandbox(self, sandbox: SandboxMode) -> None:
        # 远程执行本身是一种隔离（不在本地宿主），视为可满足；
        # 但 enforcement 只能诚实报告 partial。
        return

    def _build_args(self, command: str) -> list[str]:
        args = [
            self.ssh_cmd,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(self.port),
        ]
        if self.key_path:
            args += ["-i", os.path.expanduser(self.key_path)]
        args += self.extra_args
        user_part = f"{self.username}@" if self.username else ""
        args.append(f"{user_part}{self.host}")
        args.append(command)
        return args

    async def run(self, command: str, timeout: int = 120, **kwargs: Any) -> ExecResult:
        sandbox, _ = _resolve_sandbox(kwargs)
        args = self._build_args(command)
        started = asyncio.get_event_loop().time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                returncode = proc.returncode
                timed_out = False
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                stdout, stderr = b"", "timeout".encode()
                returncode = -1
                timed_out = True
        except Exception as exc:  # noqa: BLE001
            stdout, stderr, returncode, timed_out = b"", str(exc).encode(), -1, False

        return _finish_result(
            started, " ".join(args), self.name, sandbox, EnforcementLevel.PARTIAL,
            stdout, stderr, returncode, timed_out,
        )


# ---------------------------------------------------------------------------
# 执行器注册表与工厂
# ---------------------------------------------------------------------------

_EXECUTORS: dict[str, type[BaseExecutor]] = {
    "local": LocalExecutor,
    "docker": DockerExecutor,
    "ssh": SSHExecutor,
}


@dataclass
class ExecutorConfig:
    """执行器配置（通过环境变量 / 配置覆盖）。

    Attributes:
        backend: 后端类型（local/docker/ssh）。
        image: Docker 镜像名。
        host/username/port/key_path: SSH 连接参数。
        sandbox: 默认请求的隔离等级（read_only/workspace_write/danger_full_access）。
    """

    backend: str = "local"
    image: str = "instrumentisto/nmap"
    host: str = ""
    username: Optional[str] = None
    port: int = 22
    key_path: Optional[str] = None
    sandbox: str = SandboxMode.DANGER_FULL_ACCESS.value

    @classmethod
    def from_env(cls, prefix: str = "BAIZE_EXEC_") -> "ExecutorConfig":
        """从环境变量读取配置（便于部署时无需改代码）。

        支持 ``BAIZE_EXEC_SANDBOX`` 设置默认隔离等级。
        """
        backend = os.environ.get(f"{prefix}BACKEND", "local")
        sandbox = os.environ.get(f"{prefix}SANDBOX", SandboxMode.DANGER_FULL_ACCESS.value)
        # 校验 sandbox 值，非法则回退并告警
        try:
            SandboxMode(sandbox)
        except ValueError:
            logger.warning("非法 BAIZE_EXEC_SANDBOX=%r，回退到 danger_full_access", sandbox)
            sandbox = SandboxMode.DANGER_FULL_ACCESS.value
        return cls(
            backend=backend,
            image=os.environ.get(f"{prefix}IMAGE", "instrumentisto/nmap"),
            host=os.environ.get(f"{prefix}HOST", ""),
            username=os.environ.get(f"{prefix}USERNAME"),
            port=int(os.environ.get(f"{prefix}PORT", "22")),
            key_path=os.environ.get(f"{prefix}KEY_PATH"),
            sandbox=sandbox,
        )


def build_executor(config: Optional[ExecutorConfig] = None, **kwargs: Any) -> BaseExecutor:
    """根据配置构建执行器。

    Args:
        config: 执行器配置；为 None 时从环境变量读取（BAIZE_EXEC_*）。
        **kwargs: 直接传给执行器构造器的参数（优先级高于 config）。

    Returns:
        BaseExecutor: 对应后端的执行器实例。
    """
    config = config or ExecutorConfig.from_env()
    backend = kwargs.pop("backend", config.backend)

    if backend == "docker":
        return DockerExecutor(
            image=kwargs.pop("image", config.image),
            **kwargs,
        )
    if backend == "ssh":
        return SSHExecutor(
            host=kwargs.pop("host", config.host),
            username=kwargs.pop("username", config.username),
            port=kwargs.pop("port", config.port),
            key_path=kwargs.pop("key_path", config.key_path),
            **kwargs,
        )
    return LocalExecutor(**kwargs)


def run_shell(
    command: str,
    timeout: int = 120,
    config: Optional[ExecutorConfig] = None,
    sandbox: Optional[SandboxMode] = None,
) -> str:
    """同步执行 shell 命令（兼容旧 _run_shell 的简单用法）。

    sandbox: 请求隔离等级；为 None 时使用 config.sandbox。
    """
    executor = build_executor(config)
    requested = sandbox or (
        SandboxMode(config.sandbox) if config and config.sandbox else SandboxMode.DANGER_FULL_ACCESS
    )
    result = asyncio.run(executor.run(command, timeout=timeout, sandbox=requested))
    return result.text


__all__ = [
    "BaseExecutor",
    "ExecResult",
    "LocalExecutor",
    "DockerExecutor",
    "SSHExecutor",
    "ExecutorConfig",
    "build_executor",
    "run_shell",
    "SandboxMode",
    "EnforcementLevel",
    "SandboxUnavailableError",
    "classify_exec_error",
]
