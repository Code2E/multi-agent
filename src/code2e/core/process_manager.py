"""ProcessManager — Generated app subprocess 생명주기 (v4 §9.4, ADR-037).

DECISION:
- Q43: macOS/Linux 항상 setsid (start_new_session=True) — process group 일괄 정리.
- Q46: stdin 항상 close (DEVNULL).
- Q42: restart 는 항상 호출 (= teardown + launch 합성, 보정 #2).
- Q47: v1 health check 는 HTTP_GET / TCP_CONNECT 만 지원 (STDOUT_MATCH /
       FILE_EXISTS v1.1).

internal _processes dict 로 pid → asyncio.subprocess.Process 매핑 — proc.wait()
호출로 zombie 정확히 reap 가능 (signal 0 만으로는 zombie 인식 못 함).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from code2e.core.schemas import HealthCheckSpec, LaunchInfo, LaunchSpec

DEFAULT_TEARDOWN_GRACE_S = 5  # v4 §18.3
DEFAULT_STARTUP_TIMEOUT_S = 30
HTTP_TIMEOUT_PER_PROBE_S = 2.0


class ProcessManagerError(Exception): ...


class HealthCheckUnsupportedError(ProcessManagerError):
    """v1 미지원 health_check method (STDOUT_MATCH / FILE_EXISTS)."""


@dataclass
class ProcessManager:
    run_dir: Path
    _processes: dict[int, asyncio.subprocess.Process] = field(default_factory=dict)

    async def launch(self, spec: LaunchSpec) -> LaunchInfo:
        log_dir = self.run_dir / "app-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "stdout.log"

        # Q43: POSIX 에서만 start_new_session=True. Windows 는 NG-7 (best-effort) — 옵션 미적용.
        proc = await asyncio.create_subprocess_exec(
            *spec.command,
            cwd=spec.cwd,
            env={**os.environ, **spec.env},
            stdin=subprocess.DEVNULL,  # Q46.
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 단일 파일에 합쳐서 기록.
            start_new_session=(sys.platform != "win32"),
        )
        self._processes[proc.pid] = proc

        if proc.stdout is not None:
            asyncio.create_task(_stream_to_file(proc.stdout, log_path))

        port = spec.port_hint
        base_url = (
            f"http://localhost:{port}"
            if (spec.kind == "http" and port is not None)
            else None
        )
        return LaunchInfo(
            pid=proc.pid,
            port=port,
            base_url=base_url,
            started_at=datetime.now(UTC),
            log_path=str(log_path),
        )

    async def health_check(
        self,
        info: LaunchInfo,
        spec: HealthCheckSpec,
        timeout_s: int = DEFAULT_STARTUP_TIMEOUT_S,
    ) -> bool:
        """polling. timeout 안에 한 번이라도 통과하면 True, 그 외 False.

        Q47: HTTP_GET / TCP_CONNECT 만 v1 구현. 그 외 method 는 HealthCheckUnsupportedError.
        """
        if spec.method not in ("HTTP_GET", "TCP_CONNECT"):
            raise HealthCheckUnsupportedError(f"v1 미지원 method: {spec.method}")

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_s
        interval = max(spec.interval_ms / 1000.0, 0.05)  # 최소 50ms.
        while loop.time() < deadline:
            if await self._check_once(info, spec):
                return True
            await asyncio.sleep(interval)
        return False

    async def _check_once(self, info: LaunchInfo, spec: HealthCheckSpec) -> bool:
        if spec.method == "HTTP_GET":
            if not info.base_url:
                return False
            url = info.base_url + spec.target
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_PER_PROBE_S) as client:
                    resp = await client.get(url)
            except (httpx.HTTPError, OSError):
                return False
            return resp.status_code in spec.expected_status

        # TCP_CONNECT.
        if info.port is None:
            return False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", info.port),
                timeout=HTTP_TIMEOUT_PER_PROBE_S,
            )
        except (TimeoutError, OSError):
            return False
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True

    async def teardown(
        self,
        info: LaunchInfo,
        grace_s: int = DEFAULT_TEARDOWN_GRACE_S,
    ) -> None:
        """SIGTERM → grace_s 대기 → SIGKILL → proc.wait() 로 zombie reap.

        process group 전체에 send (Q43 setsid 와 짝). 이미 죽은 경우는 noop.
        """
        proc = self._processes.get(info.pid)
        if proc is None or proc.returncode is not None:
            return

        # 1) SIGTERM (process group).
        self._send_signal(info.pid, signal.SIGTERM)

        # 2) grace_s 동안 종료 대기.
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace_s)
            return
        except TimeoutError:
            pass

        # 3) SIGKILL fallback.
        self._send_signal(info.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await proc.wait()

    async def is_alive(self, info: LaunchInfo) -> bool:
        proc = self._processes.get(info.pid)
        if proc is None:
            return False
        return proc.returncode is None

    async def restart(self, spec: LaunchSpec, info: LaunchInfo) -> LaunchInfo:
        """v4 보정 #2 + Q42: 항상 재기동. teardown + launch 합성."""
        await self.teardown(info)
        return await self.launch(spec)

    def _send_signal(self, pid: int, sig: int) -> None:
        """process group SIGTERM/SIGKILL. 이미 죽은 / 권한 없으면 silently ignore."""
        try:
            if sys.platform != "win32":
                os.killpg(pid, sig)
            else:
                os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


async def _stream_to_file(stream: asyncio.StreamReader, path: Path) -> None:
    """proc.stdout 을 파일에 append (백그라운드 task)."""
    with path.open("ab") as f:
        async for line in stream:
            f.write(line)
            f.flush()
