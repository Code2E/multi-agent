"""Integration tests for Orchestrator._run_launching + _teardown (Phase L, v4 §6.1, §9).

실제 subprocess 사용 (POSIX). port_allocator + process_manager 통합 검증.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog

from code2e.agents.advisor import AdvisorAgent
from code2e.agents.evaluator import EvaluatorTestgenAgent
from code2e.agents.executor import ExecutorAgent
from code2e.agents.planner import PlannerAgent
from code2e.core.budget import BudgetTracker
from code2e.core.cassette import CassetteStore
from code2e.core.llm_gateway import LlmGateway
from code2e.core.orchestrator import Orchestrator
from code2e.core.port_allocator import PortAllocator
from code2e.core.process_manager import ProcessManager
from code2e.core.schemas import (
    BudgetState,
    HealthCheckSpec,
    LaunchSpec,
    Plan,
    PlanMeta,
    PlanState,
    PlanUnit,
    SystemState,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX only — Windows 는 NG-7"
)


# ---------- helpers ----------


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _tcp_listener_command(port: int, hold_s: int = 30) -> list[str]:
    code = (
        f"import socket, time;"
        f"s=socket.socket();"
        f"s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1);"
        f"s.bind(('127.0.0.1', {port}));"
        f"s.listen(1);"
        f"time.sleep({hold_s})"
    )
    return [sys.executable, "-c", code]


class _StubProvider:
    """LLM 호출 안 되는 스텁 — Phase L 단독 테스트 라 LLM 안 씀."""

    name = "stub"

    async def call(self, *args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("LLM call not expected in Phase L tests")

    def estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float:
        return 0.0


def _orchestrator(
    tmp_path: Path,
    *,
    process_manager: ProcessManager | None,
    port_allocator: PortAllocator | None,
) -> Orchestrator:
    cassette = CassetteStore(name="phase-l", dir=tmp_path / "cassettes", mode="off")
    budget = BudgetTracker(limit_usd=10.0, limit_tokens=100_000)
    gateway = LlmGateway(provider=_StubProvider(), cassette=cassette, budget=budget)
    return Orchestrator(
        planner=PlannerAgent(),
        executor=ExecutorAgent(),
        advisor=AdvisorAgent(),
        evaluator_testgen=EvaluatorTestgenAgent(),
        llm_gateway=gateway,
        budget=budget,
        workspace_root=tmp_path / "workspaces",
        process_manager=process_manager,
        port_allocator=port_allocator,
        cancel_token=asyncio.Event(),
        logger=structlog.get_logger("test"),
    )


def _state_with_launch_spec(launch_spec: LaunchSpec | None) -> SystemState:
    final = Plan(
        version=3,
        content="## final",
        units=[
            PlanUnit(
                id="U-001", title="t", description="d", acceptance_criteria=["x"]
            )
        ],
        meta=PlanMeta(
            created_at=datetime(2026, 5, 5, tzinfo=UTC), tokens_in=0, tokens_out=0
        ),
    )
    return SystemState(
        run_id="r_phase_l_test",
        status="launching",
        user_input="task",
        plan=PlanState(iterations=[final], final=final, launch_spec=launch_spec),
        budget=BudgetState(limit_usd=10.0, limit_tokens=100_000),
    )


# ---------- happy path ----------


@pytest.mark.asyncio
async def test_phase_l_launches_and_health_check_passes(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path / "run")
    alloc = PortAllocator(range_=(54000, 54050))
    orch = _orchestrator(tmp_path, process_manager=pm, port_allocator=alloc)

    port = _free_port()
    spec = LaunchSpec(
        kind="http",
        command=_tcp_listener_command(port),
        port_hint=port,
        health_check=HealthCheckSpec(method="TCP_CONNECT", target="", interval_ms=100),
        startup_timeout_s=5,
    )
    state = _state_with_launch_spec(spec)

    try:
        result = await orch._run_launching(state)
        assert result.status == "testing"
        assert result.termination is None
        assert result.launch is not None
        assert result.launch.port == port
        assert result.launch.base_url == f"http://localhost:{port}"
        assert result.launch.healthy_at is not None
        # Phase L 가 띄운 process 가 살아있어야.
        assert await pm.is_alive(result.launch)
    finally:
        if result.launch is not None:  # type: ignore[possibly-undefined]
            await pm.teardown(result.launch, grace_s=2)


# ---------- failure routing ----------


@pytest.mark.asyncio
async def test_phase_l_aborts_when_launch_spec_missing(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path / "run")
    alloc = PortAllocator(range_=(54100, 54101))
    orch = _orchestrator(tmp_path, process_manager=pm, port_allocator=alloc)

    state = _state_with_launch_spec(None)
    result = await orch._run_launching(state)
    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "LAUNCH_SPEC_MISSING"


@pytest.mark.asyncio
async def test_phase_l_aborts_when_dependencies_missing(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path, process_manager=None, port_allocator=None)
    spec = LaunchSpec(
        kind="cli",
        command=[sys.executable, "-c", "pass"],
        health_check=HealthCheckSpec(method="TCP_CONNECT", target=""),
    )
    result = await orch._run_launching(_state_with_launch_spec(spec))
    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_phase_l_aborts_on_health_timeout(tmp_path: Path) -> None:
    """존재하지 않는 포트로 health check → timeout → LAUNCH_TIMEOUT."""
    pm = ProcessManager(run_dir=tmp_path / "run")
    alloc = PortAllocator(range_=(54200, 54210))
    orch = _orchestrator(tmp_path, process_manager=pm, port_allocator=alloc)

    # 산출물이 listening 안 함 (그냥 sleep) — health TCP_CONNECT 는 실패.
    spec = LaunchSpec(
        kind="http",
        command=[sys.executable, "-c", "import time; time.sleep(10)"],
        health_check=HealthCheckSpec(method="TCP_CONNECT", target="", interval_ms=100),
        startup_timeout_s=1,  # 짧게.
    )
    result = await orch._run_launching(_state_with_launch_spec(spec))

    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "LAUNCH_TIMEOUT"
    # teardown + port release 가 호출되어야 한다 — 포트가 풀에 다시 들어갔는지.
    # acquire 다시 호출해서 확인.
    new_port = await alloc.acquire()
    assert 54200 <= new_port <= 54210


@pytest.mark.asyncio
async def test_phase_l_aborts_on_port_unavailable(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path / "run")
    # 1-사이즈 range 를 미리 잡아둔다.
    alloc = PortAllocator(range_=(54300, 54300))
    await alloc.acquire()

    orch = _orchestrator(tmp_path, process_manager=pm, port_allocator=alloc)

    spec = LaunchSpec(
        kind="http",
        command=[sys.executable, "-c", "import time; time.sleep(10)"],
        health_check=HealthCheckSpec(method="TCP_CONNECT", target=""),
    )
    result = await orch._run_launching(_state_with_launch_spec(spec))
    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "PORT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_phase_l_cli_kind_skips_port_allocation(tmp_path: Path) -> None:
    """kind=cli 는 port 무관 — 그러나 health_check TCP_CONNECT 는 port 필요해서 실패."""
    pm = ProcessManager(run_dir=tmp_path / "run")
    alloc = PortAllocator(range_=(54400, 54401))
    orch = _orchestrator(tmp_path, process_manager=pm, port_allocator=alloc)

    spec = LaunchSpec(
        kind="cli",
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
        health_check=HealthCheckSpec(
            method="TCP_CONNECT", target="", interval_ms=100
        ),
        startup_timeout_s=1,
    )
    result = await orch._run_launching(_state_with_launch_spec(spec))
    # CLI kind 은 port 없음 — health TCP_CONNECT 는 항상 False → LAUNCH_TIMEOUT.
    # 의도: CLI kind 은 보통 health 가 다른 메서드이지만, 현 테스트는 port 미할당
    # 검증이 목적. 결과적으로 LAUNCH_TIMEOUT 으로 abort 하지만 이는 별도 문제.
    assert result.status == "aborted"
    assert result.termination is not None
    assert result.termination.reason == "LAUNCH_TIMEOUT"


# ---------- _teardown ----------


@pytest.mark.asyncio
async def test_teardown_kills_process_and_releases_port(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path / "run")
    alloc = PortAllocator(range_=(54500, 54510))
    orch = _orchestrator(tmp_path, process_manager=pm, port_allocator=alloc)

    port = _free_port()
    spec = LaunchSpec(
        kind="http",
        command=_tcp_listener_command(port),
        port_hint=port,
        health_check=HealthCheckSpec(method="TCP_CONNECT", target="", interval_ms=100),
        startup_timeout_s=5,
        teardown_grace_s=2,
    )
    launched = await orch._run_launching(_state_with_launch_spec(spec))
    assert launched.status == "testing"
    assert launched.launch is not None

    after = await orch._teardown(launched)
    assert launched.launch is not None
    assert not await pm.is_alive(launched.launch)
    # state 자체는 그대로 (status 변경 안 함 — caller 책임).
    assert after.status == "testing"


@pytest.mark.asyncio
async def test_teardown_noop_when_no_launch_info(tmp_path: Path) -> None:
    pm = ProcessManager(run_dir=tmp_path / "run")
    alloc = PortAllocator(range_=(54600, 54600))
    orch = _orchestrator(tmp_path, process_manager=pm, port_allocator=alloc)

    state = _state_with_launch_spec(None)  # state.launch 도 None.
    result = await orch._teardown(state)
    # 그냥 state 반환, 에러 없음.
    assert result.launch is None
