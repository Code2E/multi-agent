"""ProcessManager — Generated app subprocess 생명주기 (v4 §9.4, ADR-037).

DECISION: Q43 — macOS/Linux 항상 setsid (start_new_session=True).
DECISION: Q42 — restart 는 항상 호출 (= teardown + launch 합성, v4 §6.1 라인 961 보정).
DECISION: Q46 — stdin 은 항상 close (DEVNULL).
DECISION: Q47 — health check 4종 정의 유지하되 v1 구현은 HTTP_GET / TCP_CONNECT 만.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code2e.core.schemas import HealthCheckSpec, LaunchInfo, LaunchSpec

DEFAULT_TEARDOWN_GRACE_S = 5  # v4 §18.3


@dataclass
class ProcessManager:
    run_dir: Path

    async def launch(self, spec: LaunchSpec) -> LaunchInfo:
        raise NotImplementedError("ProcessManager.launch — v4 §9.4 구현 예정 (setsid + stdin close)")

    async def health_check(self, info: LaunchInfo, spec: HealthCheckSpec) -> bool:
        raise NotImplementedError(
            "ProcessManager.health_check — v4 §9.3 (HTTP_GET / TCP_CONNECT v1)"
        )

    async def teardown(self, info: LaunchInfo, grace_s: int = DEFAULT_TEARDOWN_GRACE_S) -> None:
        """SIGTERM → grace_s 대기 → SIGKILL. process group 전체에 send (os.killpg)."""
        raise NotImplementedError("ProcessManager.teardown — v4 §9.9 구현 예정")

    async def is_alive(self, info: LaunchInfo) -> bool:
        raise NotImplementedError("ProcessManager.is_alive — phase 2 구현 예정")

    async def restart(self, spec: LaunchSpec, info: LaunchInfo) -> LaunchInfo:
        """v4 §6.1 라인 961 보정: teardown + launch 합성.

        v4 §9.7 의 'Test Loop 내에서 코드 수정 후 항상 재기동' 정책 (Q42).
        """
        raise NotImplementedError("ProcessManager.restart — v4 보정 #2 구현 예정")
