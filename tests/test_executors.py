"""执行器与沙箱 fail-closed 测试。"""

from __future__ import annotations

import pytest

from baize.executors import (
    EnforcementLevel,
    ExecResult,
    LocalExecutor,
    SandboxMode,
    SandboxUnavailableError,
    build_executor,
    classify_exec_error,
)


def test_classify_exec_error() -> None:
    assert classify_exec_error("", 0) is None
    assert classify_exec_error("", 1) == "runner_failure"
    assert classify_exec_error("Operation not permitted", 1) == "sandbox_denied"
    assert classify_exec_error("EROFS: read-only file system", 1) == "sandbox_denied"
    assert classify_exec_error("bash: foo: command not found", 127) == "runner_failure"
    assert classify_exec_error("", 0, timed_out=True) == "timeout"


def test_execresult_text() -> None:
    r = ExecResult(command="echo hi", stdout="hi", returncode=0)
    assert r.text == "hi"
    r2 = ExecResult(command="x", returncode=-1, timed_out=True)
    assert "超时" in r2.text


@pytest.mark.asyncio
async def test_local_danger_passthrough() -> None:
    ex = LocalExecutor()
    r = await ex.run("echo hello", sandbox=SandboxMode.DANGER_FULL_ACCESS)
    assert r.text == "hello"
    assert r.enforcement == EnforcementLevel.NONE.value
    assert r.executor == "local"


@pytest.mark.asyncio
async def test_local_read_only_blocks_host_write() -> None:
    """read_only 沙箱必须真实阻止宿主文件系统写入（fail-closed 语义）。"""
    ex = LocalExecutor()
    r = await ex.run("touch $HOME/baize_should_not_exist", sandbox=SandboxMode.READ_ONLY)
    # 无 bwrap 时应 fail-closed 抛错；有 bwrap 时应被拒绝写入
    if r.returncode == -1:
        pytest.skip("执行器未能启动（可能无 bwrap）")
    assert r.returncode != 0
    assert r.enforcement == EnforcementLevel.FULL.value


@pytest.mark.asyncio
async def test_workspace_write_allows_tmp() -> None:
    ex = LocalExecutor()
    r = await ex.run(
        "echo ok > /tmp/baize_ws_test_xyz && cat /tmp/baize_ws_test_xyz",
        sandbox=SandboxMode.WORKSPACE_WRITE,
    )
    assert "ok" in r.text


@pytest.mark.asyncio
async def test_build_executor_backends() -> None:
    ex = build_executor(backend="docker", image="test/img")
    assert ex.name == "docker"
    assert ex.image == "test/img"
    ex2 = build_executor(backend="ssh", host="10.0.0.1", username="root")
    assert ex2.name == "ssh"
    assert ex2.host == "10.0.0.1"
    ex3 = build_executor(backend="local")
    assert ex3.name == "local"


def test_executor_config_from_env(monkeypatch) -> None:
    from baize.executors import ExecutorConfig

    monkeypatch.setenv("BAIZE_EXEC_SANDBOX", "read_only")
    cfg = ExecutorConfig.from_env()
    assert cfg.sandbox == "read_only"
    monkeypatch.setenv("BAIZE_EXEC_SANDBOX", "bogus_value")
    cfg2 = ExecutorConfig.from_env()
    assert cfg2.sandbox == SandboxMode.DANGER_FULL_ACCESS.value


def test_sandbox_unavailable_error_type() -> None:
    assert issubclass(SandboxUnavailableError, RuntimeError)
