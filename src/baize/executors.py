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
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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
    # 长任务（tmux）后台会话名；非空时命令可能仍在后台运行，可用会话名取回结果
    session: Optional[str] = None

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
    session: Optional[str] = None,
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
        session=session,
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
        proc = None
        try:
            if sandbox == SandboxMode.DANGER_FULL_ACCESS:
                argv = [self.shell, "-c", command]
            else:
                argv = self._wrap_bwrap(command, sandbox)
            # start_new_session: 让命令进入独立进程组，超时/取消时整组杀除，
            # 避免 ``/bin/bash -c "cmd1 | cmd2"`` 的子孙进程泄漏成孤儿（长任务场景会放大）。
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, **(self.env or {})},
                    start_new_session=True,
                )
            except TypeError:  # pragma: no cover - 极老版本 asyncio 不支持该参数
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, **(self.env or {})},
                )
            # timeout<=0 表示不限制（wait_for 传 None）
            wait_timeout = timeout if timeout and timeout > 0 else None
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=wait_timeout)
                returncode = proc.returncode
                timed_out = False
            except asyncio.TimeoutError:
                await self._terminate_tree(proc)
                stdout, stderr = b"", "timeout".encode()
                returncode = -1
                timed_out = True
        except SandboxUnavailableError:
            raise
        except asyncio.CancelledError:
            # 外层取消（如 AgentTool 兜底超时）：同样要杀进程组，避免后台残留
            if proc is not None:
                await self._terminate_tree(proc)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("本地执行失败: %s", exc)
            stdout, stderr, returncode, timed_out = b"", str(exc).encode(), -1, False

        enforcement = EnforcementLevel.FULL if sandbox != SandboxMode.DANGER_FULL_ACCESS else EnforcementLevel.NONE
        return _finish_result(
            started, command, self.name, sandbox, enforcement,
            stdout, stderr, returncode, timed_out,
        )

    @staticmethod
    async def _terminate_tree(proc: Any) -> None:
        """终止进程及其整棵进程组（SIGKILL 整个 group）。"""
        if proc.returncode is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:  # pragma: no cover
                pass
        except (ProcessLookupError, PermissionError, OSError):  # pragma: no cover
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass


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
            # timeout<=0 表示不限制（wait_for 传 None）
            wait_timeout = timeout if timeout and timeout > 0 else None
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=wait_timeout)
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
            # timeout<=0 表示不限制（wait_for 传 None）
            wait_timeout = timeout if timeout and timeout > 0 else None
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=wait_timeout)
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


class TmuxExecutor(BaseExecutor):
    """tmux 执行器 —— 长任务在独立 tmux 会话中运行，可跨超时存活。

    LocalExecutor 的硬顶超时（默认 300s，见 ``BAIZE_TOOL_EXEC_TIMEOUT``）
    会让 hashcat/john/全端口扫描这类长任务在超时后被整组杀死、结果丢失。
    ``TmuxExecutor`` 把命令放进一个 detached tmux 会话执行：
    - 输出实时落盘，超时返回时**会话继续在后台运行**，后续可取回结果；
    - 支持进程组语义：清理时 ``kill-session`` 连子孙进程一起杀掉；
    - 同一实例可管理多个命名会话（长任务之间互不干扰）。

    用法::

        ex = TmuxExecutor()
        r = await ex.run("hashcat -a 3 hash.txt", timeout=60)
        # 超时后:
        if r.timed_out and r.payload.get("session"):
            ...   # 会话仍在后台跑，稍后取结果
        r2 = await ex.run("cat /tmp/out", timeout=10, session=r.payload["session"])
        # 或主动清理
        await ex.stop(session_name)
    """

    name = "tmux"
    default_enforcement = EnforcementLevel.NONE
    # run() 超时时是否保留后台会话（长任务语义：保留，方便取回结果）
    keep_on_timeout = True

    def __init__(self, shell: str = "/bin/bash", tmux_cmd: str = "tmux") -> None:
        self.shell = shell
        self.tmux_cmd = tmux_cmd
        self._tmp_root = Path(tempfile.gettempdir()) / "baize-tmux"
        self._tmp_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 会话管理工具
    # ------------------------------------------------------------------

    def _session_dir(self, session: str) -> Path:
        return self._tmp_root / session

    @staticmethod
    def _quote_single(value: str) -> str:
        return "'" + value.replace("'", "'\\''") + "'"

    def check_available(self) -> bool:
        """tmux 是否可用（缺失时所有执行 fail-closed）。"""
        return shutil.which(self.tmux_cmd) is not None

    def has_session(self, session: str) -> bool:
        """判断命名会话是否仍在运行。"""
        if not session:
            return False
        try:
            proc = subprocess.run(
                [self.tmux_cmd, "has-session", "-t", session],
                capture_output=True, timeout=10,
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):  # noqa: BLE001
            return False

    async def _run_tmux(self, args: list[str]) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            self.tmux_cmd, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "tmux 命令超时"
        text = (stdout or b"").decode("utf-8", "replace") + (stderr or b"").decode("utf-8", "replace")
        return proc.returncode, text.strip()

    def _ensure_running_script(self, session: str, command: str) -> Path:
        """为会话生成 runner 脚本（输出/退出码落盘，便于轮询与超时后取回）。"""
        sdir = self._session_dir(session)
        sdir.mkdir(parents=True, exist_ok=True)
        out_path = sdir / "output.log"
        rc_path = sdir / "exitcode"
        # 清理历史残留
        for p in (out_path, rc_path):
            if p.exists():
                p.unlink()
        script = sdir / "run.sh"
        script.write_text(
            "#!/bin/bash\n"
            f"exec >> {self._quote_single(str(out_path))} 2>&1\n"
            "trap 'echo 130 > "
            f"{self._quote_single(str(rc_path))}"
            "' INT TERM\n"
            f"{command}\n"
            f"echo $? > {self._quote_single(str(rc_path))}\n",
            encoding="utf-8",
        )
        os.chmod(script, 0o755)
        return script

    async def run(self, command: str, timeout: int = 120, **kwargs: Any) -> ExecResult:
        """在 detached tmux 会话中运行命令。

        kwargs 额外支持:
        - ``session``: 复用指定会话名（默认自动生成 ``baize-<hex>``）。
        - ``sandbox``: 本后端不提供隔离；非 danger 请求将 fail-closed。
        """
        sandbox, _ = _resolve_sandbox(kwargs)
        self.check_sandbox(sandbox)
        if not self.check_available():
            return ExecResult(
                command=command, stderr="tmux 命令不可用，长任务执行器不可用", returncode=127,
                executor=self.name, error_kind="runner_failure",
            )
        session = kwargs.get("session") or f"baize-{uuid.uuid4().hex[:8]}"
        # 复用会话时不重复创建
        if not self.has_session(session):
            script = self._ensure_running_script(session, command)
            code, err = await self._run_tmux(
                ["new-session", "-d", "-s", session, "bash", str(script)]
            )
            if code != 0:
                return ExecResult(
                    command=command, stderr=err or "创建 tmux 会话失败",
                    returncode=code or -1, executor=self.name, error_kind="runner_failure",
                )

        started = asyncio.get_event_loop().time()
        out_path = self._session_dir(session) / "output.log"
        rc_path = self._session_dir(session) / "exitcode"
        # 轮询：等待会话结束 / 退出码文件出现
        while True:
            done = rc_path.exists() or not self.has_session(session)
            if done:
                break
            if timeout > 0 and (asyncio.get_event_loop().time() - started) >= timeout:
                break
            await asyncio.sleep(0.5)

        stdout = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
        timed_out = not (rc_path.exists() or not self.has_session(session))
        returncode = -1
        if rc_path.exists():
            try:
                returncode = int(rc_path.read_text(encoding="utf-8").strip() or "-1")
            except ValueError:
                returncode = -1

        if not timed_out:
            # 正常完成：清理会话与临时目录
            if self.has_session(session):
                await self._run_tmux(["kill-session", "-t", session])
            try:
                import shutil as _shutil

                _shutil.rmtree(self._session_dir(session), ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
            session = None  # 已完成，不再暴露后台会话

        return _finish_result(
            started, command, self.name, sandbox, EnforcementLevel.NONE,
            stdout.encode("utf-8", "replace") if stdout else b"",
            b"",
            returncode,
            timed_out,
            session=session if timed_out else None,
        )

    async def stop(self, session: str) -> None:
        """终止指定会话（进程组连带子孙一起清理）。"""
        if session and self.has_session(session):
            await self._run_tmux(["kill-session", "-t", session])

    async def poll(self, session: str) -> dict[str, Any]:
        """查询后台会话状态：{running, returncode, output}。"""
        out_path = self._session_dir(session) / "output.log"
        rc_path = self._session_dir(session) / "exitcode"
        running = self.has_session(session)
        stdout = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
        returncode = -1
        if rc_path.exists():
            try:
                returncode = int(rc_path.read_text(encoding="utf-8").strip() or "-1")
            except ValueError:
                returncode = -1
        return {"session": session, "running": running, "returncode": returncode, "output": stdout}


# ---------------------------------------------------------------------------
# 执行器注册表与工厂
# ---------------------------------------------------------------------------
_EXECUTORS: dict[str, type[BaseExecutor]] = {
    "local": LocalExecutor,
    "docker": DockerExecutor,
    "ssh": SSHExecutor,
    "tmux": TmuxExecutor,
}


@dataclass
class ExecutorConfig:
    """执行器配置（通过环境变量 / 配置覆盖）。

    Attributes:
        backend: 后端类型（local/docker/ssh/tmux）。
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
    if backend == "tmux":
        return TmuxExecutor(
            shell=kwargs.pop("shell", "/bin/bash"),
            tmux_cmd=kwargs.pop("tmux_cmd", "tmux"),
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
    "TmuxExecutor",
    "ExecutorConfig",
    "build_executor",
    "run_shell",
    "SandboxMode",
    "EnforcementLevel",
    "SandboxUnavailableError",
    "classify_exec_error",
]
