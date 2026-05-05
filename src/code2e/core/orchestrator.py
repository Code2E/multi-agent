"""Orchestrator — 전체 파이프라인 조율 + 상태 머신 (v4 §3.3, §3.5, §6.1).

상태 전이:
  IDLE → PLANNING → BUILDING → LAUNCHING → TESTING → TEARDOWN → COMPLETED
  실패 시 ABORTED (TerminationReason 기록).

Phase 1 종료 직후 asyncio.TaskGroup 으로 Build (Branch A) 와 Evaluator.testgen
(Branch B) 가 병렬 (ADR-036).

v4 보정 #4: PlanUnit.dependencies 위상 정렬 검증 (validate_unit_dag) — 순환 시
UNIT_DECOMPOSITION_FAILED.

DECISION: Q4 — Planner round 3 명시 출력 + Orchestrator 후처리 정규식 fallback.
DECISION: Q33 — DAG 위상 정렬, 순환 시 UNIT_DECOMPOSITION_FAILED.
DECISION: Q39 — 실패 리포트 raw + 길면 헤드/테일 5KB 씩 컷 (10KB 컷).
"""

from __future__ import annotations

from dataclasses import dataclass

from code2e.core.schemas import (
    Plan,
    PlanUnit,
    SystemState,
    TerminationReason,
)

FAILURE_REPORT_HEAD_BYTES = 5 * 1024  # Q39
FAILURE_REPORT_TAIL_BYTES = 5 * 1024


@dataclass
class Orchestrator:
    """파이프라인 진입점. start() 호출 1회 = 1 run."""

    async def start(self, user_input: str) -> SystemState:
        raise NotImplementedError("Orchestrator.start — phase 2 구현 예정 (v4 §6.1)")

    async def _run_planning(self, state: SystemState) -> SystemState:
        """Phase 1: 3-round Planner."""
        raise NotImplementedError("Orchestrator._run_planning — phase 2 구현 예정")

    async def _run_building_and_testgen(self, state: SystemState) -> SystemState:
        """Phase 2: TaskGroup(Build, Evaluator.testgen) (v4 §3.8)."""
        raise NotImplementedError("Orchestrator._run_building_and_testgen — phase 2 구현 예정")

    async def _run_launching(self, state: SystemState) -> SystemState:
        """Phase L: ProcessManager.launch + health-check."""
        raise NotImplementedError("Orchestrator._run_launching — phase 2 구현 예정")

    async def _run_testing(self, state: SystemState) -> SystemState:
        """Phase 3: Evaluator.testrun ↔ Executor revise loop."""
        raise NotImplementedError("Orchestrator._run_testing — phase 2 구현 예정")

    async def _teardown(self, state: SystemState) -> SystemState:
        """SIGTERM (grace 5s) → SIGKILL → port release (v4 §18)."""
        raise NotImplementedError("Orchestrator._teardown — phase 2 구현 예정")


def parse_units_from_plan(plan_text: str) -> list[PlanUnit]:
    """Q4: Planner round 3 의 frontmatter `units:` 파싱.

    1차로 YAML frontmatter 파싱, 실패 시 정규식 fallback (id/title/description 추출).
    둘 다 실패하면 빈 리스트 반환 → 호출자가 UNIT_DECOMPOSITION_FAILED 로 abort.
    """
    raise NotImplementedError("parse_units_from_plan — phase 2 구현 예정")


def validate_unit_dag(units: list[PlanUnit]) -> tuple[bool, TerminationReason | None]:
    """v4 보정 #4 + Q33: dependencies 가 valid id 인지 + 순환 검사.

    순환 또는 dangling reference → (False, "UNIT_DECOMPOSITION_FAILED").
    """
    raise NotImplementedError("validate_unit_dag — phase 2 구현 예정")


def topological_sort(units: list[PlanUnit]) -> list[PlanUnit]:
    """Q33 + Q5 (ADR-003): unit 처리 순서를 위상 정렬로 결정.

    선행 조건: validate_unit_dag 가 (True, None) 을 반환했을 때만 호출.
    """
    raise NotImplementedError("topological_sort — phase 2 구현 예정")


def truncate_failure_report(raw: str) -> str:
    """Q39: 10KB 초과 시 헤드/테일 5KB 씩 + 중간 마커."""
    raise NotImplementedError("truncate_failure_report — phase 2 구현 예정")


def extract_launch_spec_from_plan(plan: Plan) -> object | None:
    """v4 §9.2: plan frontmatter `launch:` 블록 파싱.

    Q41: workspace YAML 모두 없으면 LAUNCH_SPEC_MISSING 으로 abort (휴리스틱 v1 미포함).
    """
    raise NotImplementedError("extract_launch_spec_from_plan — phase 2 구현 예정")
