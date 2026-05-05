"""Unit tests for code2e.core.process_manager (v4 §9.4, ADR-037).

실제 subprocess (python -c) 로 테스트. flaky 회피를 위해:
- timeout 짧게 (1-2 초).
- finally 에서 항상 teardown 호출 (자식 누수 방지).
- POSIX 만 (Windows 는 NG-7 best-effort, 테스트 skip).
"""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest

from code2e.core.process_manager import (
    HealthCheckUnsupportedError,
    ProcessManager,
)
from code2e.core.schemas import HealthCheckSpec, LaunchInfo, LaunchSpec

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX only — Windows 는 NG-7 best-effort"
)


# ---------- helpers ----------


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _sleep_spec(seconds: int = 30) -> LaunchSpec:
    return LaunchSpec(
        kind="cli",
        command=[sys.executable, "-c", f"import time; time.sleep({seconds})"],
        health_check=HealthCheckSpec(method="TCP_CONNECT", target=""),
    )


def _tcp_listener_spec(port: int) -> LaunchSpec:
    """주어진 포트에 listening 하는 더미 process."""
    code = (
        f"import socket, time;"
        f"s=socket.socket();"
        f"s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1);"
        f"s.bind(('127.0.0.1', {port}));"
        f"s.listen(1);"
        f"time.sleep(30)"
    )
    return LaunchSpec(
        kind="http",
        command=[sys.executable, "-c", code],
        port_hint=port,
        health_check=HealthCheckSpec(method="TCP_CONNECT", target=""),
    )


def _sigterm_ignore_spec() -> LaunchSpec:
    """SIGTERM 무시하는 process — SIGKILL fallback 검증용."""
    code = (
        "import signal, time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "time.sleep(60)"
    )
    return LaunchSpec(
        kind="cli",
        command=[sys.executable, "-c", code],
        health_check=HealthCheckSpec(method="TCP_CONNECT", target=""),
    )


# ---------- launch / is_alive / teardown ----------


@pytest.mark.asyncio
async def test_launch_returns_launch_info(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path)
    info = await pm.launch(_sleep_spec())
    try:
        assert isinstance(info, LaunchInfo)
        assert info.pid > 0
        assert info.log_path.endswith("stdout.log")
        assert (tmp_path / "app-logs").is_dir()
        assert await pm.is_alive(info)
    finally:
        await pm.teardown(info, grace_s=2)


@pytest.mark.asyncio
async def test_launch_http_kind_sets_base_url(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path)
    port = _free_port()
    info = await pm.launch(_tcp_listener_spec(port))
    try:
        assert info.base_url == f"http://localhost:{port}"
        assert info.port == port
    finally:
        await pm.teardown(info, grace_s=2)


@pytest.mark.asyncio
async def test_launch_cli_kind_no_base_url(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path)
    info = await pm.launch(_sleep_spec())
    try:
        assert info.base_url is None
        assert info.port is None
    finally:
        await pm.teardown(info, grace_s=2)


@pytest.mark.asyncio
async def test_teardown_sigterm_terminates_quickly(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path)
    info = await pm.launch(_sleep_spec())
    await pm.teardown(info, grace_s=2)
    assert not await pm.is_alive(info)


@pytest.mark.asyncio
async def test_teardown_sigkill_fallback_when_sigterm_ignored(tmp_path: Path) -> None:
    """SIGTERM 무시 process → grace 후 SIGKILL → 죽음."""
    pm = ProcessManager(run_dir=tmp_path)
    info = await pm.launch(_sigterm_ignore_spec())
    # short grace — SIGTERM 무시되므로 SIGKILL 로 죽어야.
    await pm.teardown(info, grace_s=1)
    assert not await pm.is_alive(info)


@pytest.mark.asyncio
async def test_teardown_already_dead_is_noop(tmp_path: Path) -> None:
    """exit 0 으로 종료된 process 에 teardown 호출 → 에러 없음."""
    pm = ProcessManager(run_dir=tmp_path)
    spec = LaunchSpec(
        kind="cli",
        command=[sys.executable, "-c", "pass"],  # 즉시 종료.
        health_check=HealthCheckSpec(method="TCP_CONNECT", target=""),
    )
    info = await pm.launch(spec)
    # process 가 종료될 때까지 잠깐 대기.
    await asyncio.sleep(0.2)
    assert not await pm.is_alive(info)
    # teardown 은 noop.
    await pm.teardown(info, grace_s=1)


@pytest.mark.asyncio
async def test_is_alive_for_unknown_pid_false(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path)
    fake = LaunchInfo(
        pid=999_999_999,  # 존재하지 않는 pid.
        port=None,
        base_url=None,
        started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        log_path="/tmp/nope.log",
    )
    assert not await pm.is_alive(fake)


# ---------- health_check ----------


@pytest.mark.asyncio
async def test_health_check_tcp_connect_success(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path)
    port = _free_port()
    info = await pm.launch(_tcp_listener_spec(port))
    try:
        spec = HealthCheckSpec(method="TCP_CONNECT", target="", interval_ms=100)
        ok = await pm.health_check(info, spec, timeout_s=5)
        assert ok is True
    finally:
        await pm.teardown(info, grace_s=2)


@pytest.mark.asyncio
async def test_health_check_tcp_connect_timeout_when_port_unused(tmp_path: Path) -> None:
    """port_hint 만 있고 실제 listening 안 함 → timeout 후 False."""
    pm = ProcessManager(run_dir=tmp_path)
    info = LaunchInfo(
        pid=1,  # is_alive 와 무관 — health_check 는 socket 만 본다.
        port=_free_port(),
        base_url=None,
        started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        log_path="/tmp/x.log",
    )
    spec = HealthCheckSpec(method="TCP_CONNECT", target="", interval_ms=100)
    ok = await pm.health_check(info, spec, timeout_s=1)
    assert ok is False


@pytest.mark.asyncio
async def test_health_check_unsupported_method_raises(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path)
    info = LaunchInfo(
        pid=1,
        port=None,
        base_url=None,
        started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        log_path="/tmp/x.log",
    )
    spec = HealthCheckSpec(method="STDOUT_MATCH", target="ready", interval_ms=100)
    with pytest.raises(HealthCheckUnsupportedError, match="STDOUT_MATCH"):
        await pm.health_check(info, spec, timeout_s=1)


@pytest.mark.asyncio
async def test_health_check_http_get_no_base_url_returns_false(tmp_path: Path) -> None:
    """kind=cli 등으로 base_url 이 None 이면 HTTP_GET 항상 False."""
    pm = ProcessManager(run_dir=tmp_path)
    info = LaunchInfo(
        pid=1,
        port=None,
        base_url=None,
        started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        log_path="/tmp/x.log",
    )
    spec = HealthCheckSpec(method="HTTP_GET", target="/", interval_ms=100)
    ok = await pm.health_check(info, spec, timeout_s=1)
    assert ok is False


# ---------- restart (보정 #2) ----------


@pytest.mark.asyncio
async def test_restart_yields_new_pid(tmp_path: Path) -> None:
    """v4 보정 #2: restart = teardown + launch. 새 pid 가 나와야."""
    pm = ProcessManager(run_dir=tmp_path)
    spec = _sleep_spec()
    info1 = await pm.launch(spec)
    try:
        info2 = await pm.restart(spec, info1)
        try:
            assert info2.pid != info1.pid
            assert await pm.is_alive(info2)
            assert not await pm.is_alive(info1)  # 이전 process 는 죽음.
        finally:
            await pm.teardown(info2, grace_s=2)
    finally:
        # info1 은 이미 teardown 됐지만 안전을 위해.
        await pm.teardown(info1, grace_s=1)
