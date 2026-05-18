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

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import structlog
import yaml
from pydantic import ValidationError

from code2e.core.budget import BudgetExceededError, BudgetTracker
from code2e.core.checkpoint import CheckpointWriter
from code2e.core.llm_gateway import LlmGateway, RepairExhaustedError
from code2e.core.port_allocator import PortAllocator, PortUnavailableError
from code2e.core.process_manager import ProcessManager
from code2e.core.schemas import (
    AdvisorFeedback,
    AdvisorInput,
    BudgetState,
    BuildState,
    EvaluatorTestgenInput,
    EvaluatorTestrunInput,
    ExecutorInput,
    LaunchSpec,
    Plan,
    PlannerInput,
    PlanUnit,
    RegressionContext,
    SystemState,
    TerminationInfo,
    TerminationReason,
    TestCase,
    TestRun,
    UnitState,
)
from code2e.core.state import new_run_id
from code2e.core.termination import (
    decide_force_stop_on_empty_revise,
    is_stagnant,
    is_test_stagnant,
)
from code2e.core.workspace import PathTraversalError, Workspace

if TYPE_CHECKING:
    from code2e.agents.advisor import AdvisorAgent
    from code2e.agents.base import InvocationContext
    from code2e.agents.evaluator import EvaluatorTestgenAgent, EvaluatorTestrunAgent
    from code2e.agents.executor import ExecutorAgent
    from code2e.agents.planner import PlannerAgent

FAILURE_REPORT_HEAD_BYTES = 5 * 1024  # Q39
FAILURE_REPORT_TAIL_BYTES = 5 * 1024
PHASE2_MAX_ITERATIONS = 5  # v4 §8.2
PHASE3_MAX_ITERATIONS = 5  # v4 §8.3

# start() 의 _safe_phase 시그니처용 type alias.
_PhaseFn = Callable[[SystemState], Awaitable[SystemState]]


@dataclass
class Orchestrator:
    """파이프라인 진입점. start() 호출 1회 = 1 run.

    Phase 1 의존성: planner / llm_gateway / budget (필수).
    Phase 2 의존성: executor / advisor / evaluator_testgen / workspace_root (default None,
    Phase 2 진입 시 None 이면 INTERNAL_ERROR).
    """

    planner: "PlannerAgent"
    llm_gateway: LlmGateway
    budget: BudgetTracker
    executor: "ExecutorAgent | None" = None
    advisor: "AdvisorAgent | None" = None
    evaluator_testgen: "EvaluatorTestgenAgent | None" = None
    evaluator_testrun: "EvaluatorTestrunAgent | None" = None
    workspace_root: Path | None = None
    process_manager: ProcessManager | None = None
    port_allocator: PortAllocator | None = None
    checkpoint: CheckpointWriter | None = None
    cancel_token: asyncio.Event = field(default_factory=asyncio.Event)
    logger: structlog.stdlib.BoundLogger = field(
        default_factory=lambda: structlog.get_logger("orchestrator")
    )

    async def start(self, user_input: str, run_id: str | None = None) -> SystemState:
        """파이프라인 entry point. Phase 1 → 2 → L → 3 → Teardown 순차 실행.

        - 매 phase 종료 후 checkpoint 저장 (있으면).
        - status='aborted' 면 즉시 return — 다음 phase 진입 안 함.
        - 현재 Phase L / 3 / Teardown 은 stub (NotImplementedError) — 진입 시
          INTERNAL_ERROR 로 우아하게 abort. 별도 commit 으로 구현 예정.
        """
        rid = run_id or new_run_id()
        state = SystemState(
            run_id=rid,
            status="planning",
            user_input=user_input,
            budget=BudgetState(
                limit_usd=self.budget.limit_usd,
                limit_tokens=self.budget.limit_tokens,
            ),
        )

        # Phase 1.
        state = await self._run_planning(state)
        state = self._save_checkpoint(state, "planning")
        if state.status == "aborted":
            return state

        # Phase 2.
        state = await self._run_building_and_testgen(state)
        state = self._save_checkpoint(state, "building")
        if state.status == "aborted":
            return state

        # Phase L (stub — 별도 commit).
        state = await self._safe_phase(state, self._run_launching, "Phase L")
        state = self._save_checkpoint(state, "launching")
        if state.status == "aborted":
            return state

        # Phase 3 (stub).
        state = await self._safe_phase(state, self._run_testing, "Phase 3")
        state = self._save_checkpoint(state, "testing")
        if state.status == "aborted":
            return state

        # Teardown (stub).
        state = await self._safe_phase(state, self._teardown, "Teardown")
        state = self._save_checkpoint(state, "teardown")
        if state.status == "aborted":
            return state

        final_state = state.model_copy(update={"status": "completed"})
        final_state = self._save_checkpoint(final_state, "completed")
        return final_state

    async def _safe_phase(
        self,
        state: SystemState,
        phase_fn: "_PhaseFn",
        phase_label: str,
    ) -> SystemState:
        """phase 메서드가 NotImplementedError 면 INTERNAL_ERROR 로 우아하게 abort."""
        try:
            return await phase_fn(state)
        except NotImplementedError as e:
            return self._aborted(
                state,
                "INTERNAL_ERROR",
                phase_label,
                f"{phase_label} 미구현: {e}",
                "별도 commit 의 process_manager / playwright_runner 구현 후 재시도",
            )

    def _save_checkpoint(self, state: SystemState, phase: str) -> SystemState:
        """BudgetTracker 누적값을 state.budget 으로 sync 후 checkpoint 저장.

        BudgetTracker (실시간 한도 검사용) 와 BudgetState (SystemState 의 영구 필드)
        는 별도 객체 — sync 안 하면 cost / inspect 명령이 항상 0 보고.
        """
        synced = state.model_copy(
            update={
                "budget": state.budget.model_copy(
                    update={
                        "usd_used": self.budget.usd_used,
                        "tokens_used": self.budget.tokens_used,
                    }
                )
            }
        )
        if self.checkpoint is not None:
            self.checkpoint.save(synced, phase)
        return synced

    async def _run_planning(self, state: SystemState) -> SystemState:
        """Phase 1: 3-round Planner.

        각 round 후 state.plan.iterations 누적. round 3 후 final + units (validate_dag
        통과) + launch_spec (frontmatter `launch:` 있으면) 채움. status='building' 으로
        전이. 실패 시 _aborted() 로 status='aborted' + TerminationInfo.
        """
        plans: list[Plan] = list(state.plan.iterations)
        new_state = state

        for round_no in (1, 2, 3):
            prev_plan = plans[-1] if plans else None
            inp = PlannerInput(
                user_input=state.user_input,
                prev_plan=prev_plan,
                round=cast(Literal[1, 2, 3], round_no),
            )
            ctx = self._build_ctx(state.run_id, attempt=round_no - 1)
            try:
                plan = await self.planner.invoke(inp, ctx)
            except RepairExhaustedError as e:
                return self._aborted(
                    new_state,
                    "VALIDATION_FAILURE",
                    "Phase 1",
                    f"round {round_no}: {e}",
                    "code2e inspect <run_id> 의 agent-outputs/planner 확인",
                )
            except BudgetExceededError as e:
                return self._aborted(
                    new_state,
                    "BUDGET_EXCEEDED",
                    "Phase 1",
                    f"round {round_no}: {e}",
                    "config.budget 조정 또는 cassette mode=replay 사용",
                )
            except asyncio.CancelledError:
                return self._aborted(
                    new_state,
                    "CANCELLED",
                    "Phase 1",
                    f"round {round_no} 진행 중 취소됨",
                    "code2e run --resume <run_id> 로 재개",
                )

            plans.append(plan)
            new_state = new_state.model_copy(
                update={"plan": new_state.plan.model_copy(update={"iterations": list(plans)})}
            )

        # round 3 결과 검증.
        final = plans[-1]
        units = list(final.units)
        if not units:
            # Q4: frontmatter 또는 본문 정규식 fallback.
            units = parse_units_from_plan(final.content)
        if not units:
            return self._aborted(
                new_state,
                "UNIT_DECOMPOSITION_FAILED",
                "Phase 1",
                "round 3 결과에 units 없음 (frontmatter / 본문 정규식 모두 실패)",
                "round 3 프롬프트 점검: code2e prompt edit planner 3",
            )

        ok, _reason = validate_unit_dag(units)
        if not ok:
            return self._aborted(
                new_state,
                "UNIT_DECOMPOSITION_FAILED",
                "Phase 1",
                "DAG 검증 실패 (순환 / dangling reference / duplicate id)",
                "round 3 프롬프트 점검 — DAG 규칙 명시 강화",
            )

        if final.units != units:
            final = final.model_copy(update={"units": units})

        launch_spec = extract_launch_spec_from_plan(final)

        # HTTP 산출물로 보이는데 launch_spec 누락 → Phase L 까지 가지 말고 즉시 abort.
        # 휴리스틱: 사용자 입력에 HTTP 신호 단어 (api / server / endpoint / 등) 포함
        # AND round 3 frontmatter 에 launch 블록 없음. v4 Q41 (휴리스틱 추론 미포함)
        # 원칙은 유지 — 우리는 *생성* 휴리스틱이 아니라 *진단* 휴리스틱만 사용.
        if launch_spec is None and _looks_like_http_task(state.user_input):
            return self._aborted(
                new_state.model_copy(
                    update={"plan": new_state.plan.model_copy(update={"final": final})}
                ),
                "LAUNCH_SPEC_MISSING",
                "Phase 1",
                "user_input 이 HTTP 산출물로 보이는데 round 3 plan frontmatter 에 "
                "launch 블록이 없음. Phase L 에서 어차피 abort 됨.",
                "round 3 프롬프트의 launch frontmatter 가이드 확인. "
                "또는 task 표현을 더 명시적으로 (예: 'with REST API endpoints').",
            )

        new_plan_state = new_state.plan.model_copy(
            update={"iterations": list(plans), "final": final, "launch_spec": launch_spec}
        )
        return new_state.model_copy(update={"plan": new_plan_state, "status": "building"})

    def _build_ctx(
        self, run_id: str, attempt: int, phase: str = "p1"
    ) -> "InvocationContext":
        from code2e.agents.base import InvocationContext  # noqa: PLC0415

        return InvocationContext(
            trace_id=f"{run_id}-{phase}-r{attempt}",
            attempt=attempt,
            budget=self.budget,
            cancel_token=self.cancel_token,
            logger=self.logger,
            llm=self.llm_gateway,
        )

    def _aborted(
        self,
        state: SystemState,
        reason: TerminationReason,
        phase: str,
        details: str,
        suggested_next: str,
    ) -> SystemState:
        return state.model_copy(
            update={
                "status": "aborted",
                "termination": TerminationInfo(
                    reason=reason,
                    phase=phase,
                    details=details,
                    suggested_next=suggested_next,
                ),
            }
        )

    async def _run_building_and_testgen(self, state: SystemState) -> SystemState:
        """Phase 2: TaskGroup(Build branch A, Evaluator.testgen branch B) (v4 §3.8, ADR-036).

        Branch A: topological_sort 로 unit 순서 결정 후 unit 별 Executor↔Advisor 루프
                  (max 5, stagnation/Q11 force-stop, security violation phase abort).
        Branch B: Evaluator.testgen → list[TestCase].

        성공 시 status='launching'. 실패 라우팅:
        - SECURITY_VIOLATION: phase abort (즉시).
        - VALIDATION_FAILURE / BUDGET_EXCEEDED: phase abort.
        - 일부 unit force_stopped (max iter / stagnation): phase 는 계속 (다음 unit).
        """
        if (
            self.executor is None
            or self.advisor is None
            or self.evaluator_testgen is None
            or self.workspace_root is None
        ):
            return self._aborted(
                state,
                "INTERNAL_ERROR",
                "Phase 2",
                "Phase 2 의존성 미주입 (executor / advisor / evaluator_testgen / workspace_root)",
                "Orchestrator 인스턴스 생성 시 phase 2 인자 모두 전달",
            )

        if state.plan.final is None:
            return self._aborted(
                state,
                "INTERNAL_ERROR",
                "Phase 2",
                "plan.final 없음 (phase 1 미완)",
                "phase 1 완료 후 phase 2 진입",
            )

        units_sorted = topological_sort(state.plan.final.units)
        workspace = Workspace(root=self.workspace_root / state.run_id)

        # Python 3.12 except* 블록은 return/break/continue 미허용 (PEP 654) →
        # 종료 사유를 변수에 저장 후 try 밖에서 분기.
        abort: tuple[TerminationReason, str, str] | None = None
        try:
            async with asyncio.TaskGroup() as tg:
                build_task = tg.create_task(
                    self._run_build_branch(state, units_sorted, workspace)
                )
                testgen_task = tg.create_task(self._run_testgen_branch(state))
        except* RepairExhaustedError as eg:
            abort = (
                "VALIDATION_FAILURE",
                f"{eg.exceptions[0]}",
                "code2e inspect <run_id> 의 agent-outputs 확인",
            )
        except* BudgetExceededError as eg:
            abort = (
                "BUDGET_EXCEEDED",
                f"{eg.exceptions[0]}",
                "config.budget 조정 또는 cassette mode=replay 사용",
            )
        except* asyncio.CancelledError:
            abort = (
                "CANCELLED",
                "build / testgen 진행 중 취소",
                "code2e run --resume <run_id> 로 재개",
            )

        if abort is not None:
            reason, details, suggested = abort
            return self._aborted(state, reason, "Phase 2", details, suggested)

        new_build_state, critical_reason = build_task.result()
        test_cases = testgen_task.result()

        if critical_reason is not None:
            partial = state.model_copy(update={"build": new_build_state})
            return self._aborted(
                partial,
                critical_reason,
                "Phase 2",
                f"build branch 가 critical termination 발생: {critical_reason}",
                "code2e inspect <run_id> 의 agent-outputs 확인",
            )

        new_test_state = state.test.model_copy(update={"suite": test_cases})
        return state.model_copy(
            update={
                "build": new_build_state,
                "test": new_test_state,
                "status": "launching",
            }
        )

    async def _run_build_branch(
        self,
        state: SystemState,
        units: list[PlanUnit],
        workspace: Workspace,
    ) -> tuple[BuildState, TerminationReason | None]:
        """unit 순차 처리. SECURITY_VIOLATION 발생 시 phase 전체 abort 신호."""
        unit_states: list[UnitState] = []
        critical_reason: TerminationReason | None = None
        for unit in units:
            ust = await self._build_unit(state, unit, workspace)
            unit_states.append(ust)
            if ust.force_stop_reason == "SECURITY_VIOLATION":
                critical_reason = "SECURITY_VIOLATION"
                break  # 즉시 phase abort.
        return BuildState(units=unit_states), critical_reason

    async def _build_unit(
        self,
        state: SystemState,
        unit: PlanUnit,
        workspace: Workspace,
    ) -> UnitState:
        """단일 unit 의 Executor↔Advisor 루프. max 5 iter."""
        assert self.executor is not None and self.advisor is not None  # caller 가 검증.

        feedback_history: list[AdvisorFeedback] = []
        last_feedback: AdvisorFeedback | None = None

        for iteration in range(PHASE2_MAX_ITERATIONS):
            ctx = self._build_ctx(state.run_id, attempt=iteration, phase="p2")

            # 1) Executor.invoke (현재 워크스페이스 + 직전 피드백).
            files = workspace.snapshot()
            try:
                change = await self.executor.invoke(
                    ExecutorInput(unit=unit, files=files, feedback=last_feedback),
                    ctx,
                )
            except RepairExhaustedError:
                return UnitState(
                    unit_id=unit.id,
                    status="force_stopped",
                    iteration=iteration,
                    feedback_history=list(feedback_history),
                    force_stop_reason="VALIDATION_FAILURE",
                )

            # 2) workspace 적용 (path traversal → SECURITY_VIOLATION).
            try:
                workspace.apply(change.files)
            except PathTraversalError:
                return UnitState(
                    unit_id=unit.id,
                    status="force_stopped",
                    iteration=iteration + 1,
                    feedback_history=list(feedback_history),
                    force_stop_reason="SECURITY_VIOLATION",
                )

            # 3) Advisor.invoke (변경 후 코드 + 누적 피드백).
            files_after = workspace.snapshot()
            try:
                feedback = await self.advisor.invoke(
                    AdvisorInput(
                        unit=unit, code=files_after, prior_feedback=list(feedback_history)
                    ),
                    ctx,
                )
            except RepairExhaustedError:
                return UnitState(
                    unit_id=unit.id,
                    status="force_stopped",
                    iteration=iteration + 1,
                    feedback_history=list(feedback_history),
                    force_stop_reason="VALIDATION_FAILURE",
                )

            feedback_history.append(feedback)

            # 4) 종료 판정.
            if feedback.decision == "approve":
                return UnitState(
                    unit_id=unit.id,
                    status="approved",
                    iteration=iteration + 1,
                    feedback_history=list(feedback_history),
                )

            # Q11: revise + 빈 코멘트 → force-stop.
            if decide_force_stop_on_empty_revise(feedback):
                return UnitState(
                    unit_id=unit.id,
                    status="force_stopped",
                    iteration=iteration + 1,
                    feedback_history=list(feedback_history),
                    force_stop_reason="STAGNATION",
                )

            # Q12: stagnation 검사.
            if is_stagnant(feedback_history):
                return UnitState(
                    unit_id=unit.id,
                    status="force_stopped",
                    iteration=iteration + 1,
                    feedback_history=list(feedback_history),
                    force_stop_reason="STAGNATION",
                )

            last_feedback = feedback

        # max iter 도달.
        return UnitState(
            unit_id=unit.id,
            status="force_stopped",
            iteration=PHASE2_MAX_ITERATIONS,
            feedback_history=list(feedback_history),
            force_stop_reason="MAX_ITERATIONS",
        )

    async def _run_testgen_branch(self, state: SystemState) -> list[TestCase]:
        """testgen branch — Final Plan → list[TestCase]."""
        assert self.evaluator_testgen is not None and state.plan.final is not None
        ctx = self._build_ctx(state.run_id, attempt=0, phase="p2-tg")
        return await self.evaluator_testgen.invoke(
            EvaluatorTestgenInput(final_plan=state.plan.final), ctx
        )

    async def _run_launching(self, state: SystemState) -> SystemState:
        """Phase L: PortAllocator.acquire + ProcessManager.launch + health-check (v4 §6.1, §9).

        실패 라우팅:
        - launch_spec 없음 → LAUNCH_SPEC_MISSING (Q41).
        - 의존성 미주입 → INTERNAL_ERROR.
        - 포트 고갈 → PORT_UNAVAILABLE.
        - health timeout → 정리 (teardown + port release) 후 LAUNCH_TIMEOUT.
        - health 통과 직후 process 죽음 → APP_CRASHED.

        성공 시 status='testing', state.launch=LaunchInfo (healthy_at 채움).
        """
        if state.plan.launch_spec is None:
            return self._aborted(
                state,
                "LAUNCH_SPEC_MISSING",
                "Phase L",
                "plan frontmatter / workspace YAML 모두 launch 블록 없음 (Q41 v1: 휴리스틱 미포함)",
                "code2e prompt edit planner 3 — round 3 의 launch 블록 가이드 점검",
            )
        if self.process_manager is None or self.port_allocator is None:
            return self._aborted(
                state,
                "INTERNAL_ERROR",
                "Phase L",
                "process_manager / port_allocator 의존성 미주입",
                "Orchestrator 인스턴스 생성 시 phase L 인자 모두 전달",
            )

        spec = state.plan.launch_spec

        # 1) 포트 할당 (kind=http 일 때 의미 있음. cli/worker 는 hint=None 으로 noop 가능).
        port: int | None = None
        if spec.kind == "http":
            try:
                port = await self.port_allocator.acquire(hint=spec.port_hint)
            except PortUnavailableError as e:
                return self._aborted(
                    state,
                    "PORT_UNAVAILABLE",
                    "Phase L",
                    str(e),
                    "config.generated_app.port_range 변경",
                )

        # 2) launch: cwd / command / env 를 환경 독립적으로 주입.
        #    - cwd: workspace dir (LLM 은 run_id 를 모르므로 직접 작성 불가)
        #    - command[0] "python"/"python3" → sys.executable (code2e venv 의 인터프리터).
        #      호스트 PATH 의 python 이 어떤 인터프리터일지 결정적이지 않으므로 명시적으로 박음.
        #      산출 앱 의 런타임 deps (fastapi / uvicorn 등) 는 demo extra 로 venv 에 동봉.
        #    - env: host VIRTUAL_ENV / PYTHONHOME / PYTHONPATH 누출 차단.
        #      ProcessManager 가 {**os.environ, **spec.env} 로 merge 하므로 빈 문자열로 덮어쓰기.
        #      PYTHONPATH 는 code2e 의 src 가 박혀 있어 산출 앱에 누출되면 import 충돌 가능.
        #    - PORT: 할당된 포트를 env 로 전달 (planner 프롬프트가 이걸 읽도록 안내).
        workspace_dir = (self.workspace_root or Path(".")) / state.run_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        command = list(spec.command)
        if command and command[0] in ("python", "python3"):
            command[0] = sys.executable

        launch_env: dict[str, str] = {
            **spec.env,
            "VIRTUAL_ENV": "",
            "PYTHONHOME": "",
            "PYTHONPATH": "",
        }
        if port is not None:
            launch_env["PORT"] = str(port)

        spec_overrides: dict[str, object] = {
            "cwd": str(workspace_dir),
            "command": command,
            "env": launch_env,
        }
        if port is not None:
            spec_overrides["port_hint"] = port
        spec_with_overrides = spec.model_copy(update=spec_overrides)
        info = await self.process_manager.launch(spec_with_overrides)

        # 3) health check (startup_timeout_s 동안 polling).
        ok = await self.process_manager.health_check(
            info, spec.health_check, timeout_s=spec.startup_timeout_s
        )
        if not ok:
            log_tail = _tail_log(info.log_path, n=15)
            await self.process_manager.teardown(info, grace_s=spec.teardown_grace_s)
            if port is not None:
                await self.port_allocator.release(port)
            return self._aborted(
                state,
                "LAUNCH_TIMEOUT",
                "Phase L",
                f"산출물이 {spec.startup_timeout_s}s 안에 health check 통과 못함 "
                f"(expected port={port}).\n"
                f"--- last {log_tail.count(chr(10))} log lines ---\n{log_tail}",
                f"앱이 PORT env({port}) 와 다른 포트에서 listen 중이거나 health "
                f"endpoint 응답 불가. 전체 로그: tail {info.log_path}",
            )

        # 4) health 직후 사망 검사 (race condition: TCP listening 후 즉시 crash).
        if not await self.process_manager.is_alive(info):
            log_tail = _tail_log(info.log_path, n=15)
            if port is not None:
                await self.port_allocator.release(port)
            return self._aborted(
                state,
                "APP_CRASHED",
                "Phase L",
                f"health check 직후 산출물 비정상 종료.\n"
                f"--- last {log_tail.count(chr(10))} log lines ---\n{log_tail}",
                f"전체 로그: tail {info.log_path}",
            )

        # 5) healthy_at 업데이트.
        info = info.model_copy(update={"healthy_at": datetime.now(UTC)})

        return state.model_copy(update={"launch": info, "status": "testing"})

    async def _teardown(self, state: SystemState) -> SystemState:
        """Phase Teardown: ProcessManager.teardown (SIGTERM grace SIGKILL) + port release.

        v4 §18.3 의 grace 5s. launch_spec.teardown_grace_s 우선.
        실패해도 phase 결과는 보존 — teardown 자체가 실패해도 next state 는 유지.
        """
        info = state.launch
        if info is None:
            return state  # nothing to teardown.

        if self.process_manager is not None:
            grace_s = (
                state.plan.launch_spec.teardown_grace_s
                if state.plan.launch_spec is not None
                else 5
            )
            await self.process_manager.teardown(info, grace_s=grace_s)

        if self.port_allocator is not None and info.port is not None:
            await self.port_allocator.release(info.port)

        return state

    async def _run_testing(self, state: SystemState) -> SystemState:
        """Phase 3: Evaluator.testrun ↔ Executor revise loop (v4 §6.1, §8.3).

        흐름 (max PHASE3_MAX_ITERATIONS=5):
        1. Evaluator.testrun → TestRun (results + signature).
        2. 모두 pass → status='completed'.
        3. is_test_stagnant → STAGNATION abort.
        4. 회귀 감지 (이전 통과 → 현재 실패): regression_context 구성.
        5. Executor.invoke (test_failure + regression_context) → CodeChange.
        6. workspace.apply (path traversal → SECURITY_VIOLATION abort).
        7. ProcessManager.restart (Q42, 항상 재기동) — pm 미주입이면 skip.
        8. health_check 재검증 — 실패 시 LAUNCH_TIMEOUT abort.

        v4 §8.3 회귀 정책 (ADR-039): auto-rollback OFF — 회귀 정보를 Executor 에
        전달만 하고 모델이 직접 판단해 수정 (자동 revert 안 함).
        v4 Q34: v1 은 1 회 실행 (flaky 다수결 v1.1).
        """
        if (
            self.evaluator_testrun is None
            or self.executor is None
            or self.workspace_root is None
        ):
            return self._aborted(
                state,
                "INTERNAL_ERROR",
                "Phase 3",
                "evaluator_testrun / executor / workspace_root 의존성 미주입",
                "Orchestrator 인스턴스 생성 시 phase 3 인자 모두 전달",
            )
        if state.test.suite is None:
            return self._aborted(
                state,
                "INTERNAL_ERROR",
                "Phase 3",
                "test.suite 없음 (Phase 2 testgen 미완)",
                "phase 2 완료 확인",
            )
        if state.launch is None:
            return self._aborted(
                state,
                "INTERNAL_ERROR",
                "Phase 3",
                "launch 정보 없음 (Phase L 미완)",
                "phase L 완료 확인",
            )

        workspace = Workspace(root=self.workspace_root / state.run_id)
        runs: list[TestRun] = []
        previously_passed: set[str] = set()
        # 첫 unit 만 단순화 (v1.1 에서 case → unit 매핑 정교화).
        target_unit = (
            state.plan.final.units[0]
            if state.plan.final is not None and state.plan.final.units
            else None
        )
        if target_unit is None:
            return self._aborted(
                state,
                "INTERNAL_ERROR",
                "Phase 3",
                "target unit 없음",
                "Plan units 확인",
            )

        # launch_info / suite 변수로 narrow 유지 — loop 안 state.model_copy 후에도 타입 추론 안전.
        launch_info = state.launch
        suite = state.test.suite

        await self.evaluator_testrun.runner.setup(workspace.root)
        try:
            for iteration in range(PHASE3_MAX_ITERATIONS):
                ctx = self._build_ctx(state.run_id, attempt=iteration, phase="p3")

                # 1) testrun.
                run = await self.evaluator_testrun.invoke(
                    EvaluatorTestrunInput(
                        workspace=str(workspace.root),
                        suite=suite,
                        base_url=launch_info.base_url,
                    ),
                    ctx,
                )
                run = run.model_copy(update={"iteration": iteration + 1})
                runs.append(run)

                # 2) 모두 pass?
                if run.summary.failed == 0 and run.summary.errored == 0:
                    return state.model_copy(
                        update={
                            "test": state.test.model_copy(
                                update={"runs": list(runs), "status": "passed"}
                            ),
                            "status": "completed",
                        }
                    )

                # 3) stagnation?
                if is_test_stagnant(runs):
                    return self._abort_phase3(
                        state,
                        runs,
                        "STAGNATION",
                        "동일 실패 signature 가 window 회 이상 반복",
                        "code2e inspect <run_id> 의 test-artifacts 확인",
                    )

                # 4) 회귀 감지 (ADR-039: 자동 revert 안 함, Executor 에 정보 전달).
                currently_passed = {
                    r.case_id for r in run.results if r.status == "passed"
                }
                regressed = previously_passed - currently_passed
                regression_ctx = (
                    RegressionContext(
                        previously_passing_case_ids=sorted(regressed),
                        note="이전에 통과하던 케이스가 현재 실패",
                    )
                    if regressed
                    else None
                )

                # 5) Executor revise.
                files = workspace.snapshot()
                try:
                    change = await self.executor.invoke(
                        ExecutorInput(
                            unit=target_unit,
                            files=files,
                            test_failure=run,
                            regression_context=regression_ctx,
                        ),
                        ctx,
                    )
                except RepairExhaustedError:
                    return self._abort_phase3(
                        state,
                        runs,
                        "VALIDATION_FAILURE",
                        "Executor repair 소진",
                        "code2e inspect <run_id> 의 agent-outputs/executor 확인",
                    )

                # 6) workspace 적용.
                try:
                    workspace.apply(change.files)
                except PathTraversalError:
                    return self._abort_phase3(
                        state,
                        runs,
                        "SECURITY_VIOLATION",
                        "Phase 3 Executor 가 path traversal 시도",
                        "executor 프롬프트 강화",
                    )

                # 7) process restart (Q42 항상 재기동).
                if self.process_manager is not None and state.plan.launch_spec is not None:
                    spec = state.plan.launch_spec.model_copy(
                        update={"port_hint": launch_info.port}
                    )
                    new_info = await self.process_manager.restart(spec, launch_info)
                    ok = await self.process_manager.health_check(
                        new_info,
                        spec.health_check,
                        timeout_s=spec.startup_timeout_s,
                    )
                    if not ok:
                        return self._abort_phase3(
                            state,
                            runs,
                            "LAUNCH_TIMEOUT",
                            "restart 후 산출물 health 미통과",
                            f"tail {new_info.log_path}",
                        )
                    if not await self.process_manager.is_alive(new_info):
                        return self._abort_phase3(
                            state,
                            runs,
                            "APP_CRASHED",
                            "restart 후 산출물 즉시 비정상 종료",
                            f"tail {new_info.log_path}",
                        )
                    new_info = new_info.model_copy(update={"healthy_at": datetime.now(UTC)})
                    launch_info = new_info
                    state = state.model_copy(update={"launch": new_info})

                previously_passed = currently_passed

            # max iter 도달.
            return self._abort_phase3(
                state,
                runs,
                "MAX_ITERATIONS",
                f"{PHASE3_MAX_ITERATIONS} iter 모두 실패",
                "code2e prompt edit advisor 또는 max 조정",
            )
        finally:
            await self.evaluator_testrun.runner.teardown()

    def _abort_phase3(
        self,
        state: SystemState,
        runs: list[TestRun],
        reason: TerminationReason,
        details: str,
        suggested_next: str,
    ) -> SystemState:
        """Phase 3 의 부분 실행 결과 (test.runs) 보존하면서 abort."""
        new_test = state.test.model_copy(
            update={"runs": list(runs), "status": "force_stopped"}
        )
        return state.model_copy(
            update={
                "test": new_test,
                "status": "aborted",
                "termination": TerminationInfo(
                    reason=reason,
                    phase="Phase 3",
                    details=details,
                    suggested_next=suggested_next,
                ),
            }
        )


# --- frontmatter 파싱 utilities ---

# 문서 시작의 `---\n ... \n---\n` 블록만 인식. 본문 중간의 `---` 는 무시.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)

# Fallback 정규식: 본문 헤더 형식 `## U-NNN: title` / `* U-NNN: title` / `- U-NNN: title`.
_UNIT_HEADER_RE = re.compile(r"^[#*\-]+\s*(U-\d+)\s*[:\-]\s*(.+?)\s*$", re.MULTILINE)


def _extract_frontmatter(text: str) -> str | None:
    """문서 처음의 YAML frontmatter 본문 (`---` 사이) 반환. 없으면 None."""
    m = _FRONTMATTER_RE.match(text)
    return m.group(1) if m else None


_HTTP_TASK_KEYWORDS = re.compile(
    r"\b(api|rest|server|endpoint|http|fastapi|flask|django|uvicorn|"
    r"webhook|microservice|backend|/health|/todos|GET\s|POST\s|PUT\s|DELETE\s)\b",
    re.IGNORECASE,
)


def _looks_like_http_task(user_input: str) -> bool:
    """task 가 HTTP 산출물을 요구하는지 추정.

    *생성* 휴리스틱 (LaunchSpec 추론) 이 아니라 *진단* 휴리스틱 (launch_spec 누락이
    실수인지 의도인지 판별). Q41 의 비목표 영역과 충돌하지 않음.
    false positive 는 가능하지만 false negative 는 피하도록 키워드 넉넉히 잡음.
    """
    return bool(_HTTP_TASK_KEYWORDS.search(user_input))


def _tail_log(log_path: str, n: int = 15) -> str:
    """app-logs 파일 마지막 N 줄을 안전하게 읽어 반환. 파일이 없거나 읽기 실패 시 안내 문자열.

    Phase L abort 시 진단 자동화용. uvicorn 의 "Running on http://..." / Python
    traceback / "Address already in use" 같은 결정적 단서가 마지막 영역에 모이는
    경향이라 tail 만으로도 80% 케이스 진단 가능.
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, FileNotFoundError):
        return "(log unavailable)"
    if not lines:
        return "(log empty)"
    return "".join(lines[-n:]).rstrip()


def _parse_yaml_dict(yaml_text: str) -> dict[str, object] | None:
    """YAML 텍스트를 dict 로 파싱. 파싱 실패 또는 dict 가 아니면 None."""
    try:
        data: object = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def parse_units_from_plan(plan_text: str) -> list[PlanUnit]:
    """Q4: Planner round 3 의 `units:` 파싱.

    1차: frontmatter YAML 의 `units:` 키 → PlanUnit 검증.
    2차 fallback: 본문에서 `## U-NNN: title` 형식 정규식 추출 (id/title 만 채움).

    둘 다 실패면 빈 리스트 반환 — 호출자가 UNIT_DECOMPOSITION_FAILED 로 abort.
    """
    fm = _extract_frontmatter(plan_text)
    if fm is not None:
        data = _parse_yaml_dict(fm)
        if data is not None and isinstance(data.get("units"), list):
            parsed: list[PlanUnit] = []
            for item in data["units"]:  # type: ignore[union-attr]
                if not isinstance(item, dict):
                    continue
                try:
                    parsed.append(PlanUnit.model_validate(item))
                except ValidationError:
                    continue
            if parsed:
                return parsed

    # Fallback: 본문 헤더 정규식. id 와 title 만 채우고 나머지는 빈값.
    fallback: list[PlanUnit] = []
    for match in _UNIT_HEADER_RE.finditer(plan_text):
        uid, title = match.group(1), match.group(2).strip()
        try:
            fallback.append(
                PlanUnit(
                    id=uid,
                    title=title,
                    description="",
                    acceptance_criteria=[],
                )
            )
        except ValidationError:
            continue
    return fallback


def extract_launch_spec_from_plan(plan: Plan) -> LaunchSpec | None:
    """v4 §9.2: plan frontmatter `launch:` 블록 파싱.

    Q41: workspace YAML / heuristic 추론은 v1 미포함. 이 함수가 None 반환 +
    workspace `code2e.launch.yaml` 도 없으면 호출자가 LAUNCH_SPEC_MISSING 로 abort.
    """
    fm = _extract_frontmatter(plan.content)
    if fm is None:
        return None
    data = _parse_yaml_dict(fm)
    if data is None:
        return None
    launch_data = data.get("launch")
    if not isinstance(launch_data, dict):
        return None
    try:
        return LaunchSpec.model_validate(launch_data)
    except ValidationError:
        return None


def validate_unit_dag(units: list[PlanUnit]) -> tuple[bool, TerminationReason | None]:
    """v4 보정 #4 + Q33: dependencies 가 valid id 인지 + 순환 검사.

    검사 1: 모든 unit 의 dependencies id 가 다른 unit.id 에 존재 (dangling reference).
    검사 2: 의존성 그래프에 순환이 없음 (DFS 3-color cycle detection).
    검사 3: unit.id 중복 없음.

    위반 시 (False, "UNIT_DECOMPOSITION_FAILED") 반환. 통과 시 (True, None).
    """
    ids = [u.id for u in units]
    if len(set(ids)) != len(ids):
        return False, "UNIT_DECOMPOSITION_FAILED"  # 중복 id

    id_set = set(ids)
    by_id = {u.id: u for u in units}

    # 검사 1: dangling reference.
    for u in units:
        for dep in u.dependencies:
            if dep not in id_set:
                return False, "UNIT_DECOMPOSITION_FAILED"

    # 검사 2: 3-color DFS cycle detection. white=0 / gray=1 / black=2.
    color = dict.fromkeys(ids, 0)

    def has_cycle(node: str) -> bool:
        color[node] = 1  # gray (진입)
        for dep in by_id[node].dependencies:
            if color[dep] == 1:
                return True  # back edge → 순환
            if color[dep] == 0 and has_cycle(dep):
                return True
        color[node] = 2  # black (완료)
        return False

    for u in units:
        if color[u.id] == 0 and has_cycle(u.id):
            return False, "UNIT_DECOMPOSITION_FAILED"

    return True, None


def topological_sort(units: list[PlanUnit]) -> list[PlanUnit]:
    """Q33 + Q5 (ADR-003): unit 처리 순서를 위상 정렬로 결정 (Kahn's algorithm).

    선행 조건: `validate_unit_dag(units) == (True, None)` 일 때만 호출. 호출자가
    DAG 검증을 먼저 통과시켜야 함. 위반 시 동작은 미정 (caller 책임).

    동률은 입력 순서를 따른다 (안정 정렬).
    """
    by_id = {u.id: u for u in units}
    in_degree = {u.id: len(u.dependencies) for u in units}

    # 입력 순서 유지를 위해 list 로 처리 (heapq 대신).
    queue: list[str] = [u.id for u in units if in_degree[u.id] == 0]
    sorted_ids: list[str] = []

    while queue:
        node = queue.pop(0)
        sorted_ids.append(node)
        # node 의 out-edge 들: node 를 dependency 로 갖는 unit.
        for v in units:
            if node in v.dependencies:
                in_degree[v.id] -= 1
                if in_degree[v.id] == 0:
                    queue.append(v.id)

    return [by_id[i] for i in sorted_ids]


def truncate_failure_report(raw: str) -> str:
    """Q39: 10KB 초과 시 헤드 5KB + 중간 마커 + 테일 5KB.

    바이트 기준 (utf-8 인코딩 후) 으로 컷한다. 마커는 잘린 바이트 수 포함.
    경계가 utf-8 multi-byte 중간이면 안전한 위치까지 줄여 자른다.
    """
    encoded = raw.encode("utf-8")
    total = len(encoded)
    if total <= FAILURE_REPORT_HEAD_BYTES + FAILURE_REPORT_TAIL_BYTES:
        return raw

    head = _safe_decode(encoded[:FAILURE_REPORT_HEAD_BYTES])
    tail = _safe_decode_from_end(encoded[-FAILURE_REPORT_TAIL_BYTES:])
    omitted = total - FAILURE_REPORT_HEAD_BYTES - FAILURE_REPORT_TAIL_BYTES
    return f"{head}\n\n... [{omitted} bytes truncated] ...\n\n{tail}"


def _safe_decode(buf: bytes) -> str:
    """utf-8 multi-byte 끝부분이 잘린 경우 마지막 valid byte 까지만."""
    for i in range(len(buf), max(0, len(buf) - 4) - 1, -1):
        try:
            return buf[:i].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return ""


def _safe_decode_from_end(buf: bytes) -> str:
    """utf-8 multi-byte 시작부분이 잘린 경우 첫 valid byte 부터."""
    for i in range(0, min(4, len(buf)) + 1):
        try:
            return buf[i:].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return ""
