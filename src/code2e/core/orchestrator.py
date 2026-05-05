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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import structlog
import yaml
from pydantic import ValidationError

from code2e.core.budget import BudgetExceededError, BudgetTracker
from code2e.core.llm_gateway import LlmGateway, RepairExhaustedError
from code2e.core.schemas import (
    AdvisorFeedback,
    AdvisorInput,
    BuildState,
    EvaluatorTestgenInput,
    ExecutorInput,
    LaunchSpec,
    Plan,
    PlannerInput,
    PlanUnit,
    SystemState,
    TerminationInfo,
    TerminationReason,
    TestCase,
    UnitState,
)
from code2e.core.termination import decide_force_stop_on_empty_revise, is_stagnant
from code2e.core.workspace import PathTraversalError, Workspace

if TYPE_CHECKING:
    from code2e.agents.advisor import AdvisorAgent
    from code2e.agents.base import InvocationContext
    from code2e.agents.evaluator import EvaluatorTestgenAgent
    from code2e.agents.executor import ExecutorAgent
    from code2e.agents.planner import PlannerAgent

FAILURE_REPORT_HEAD_BYTES = 5 * 1024  # Q39
FAILURE_REPORT_TAIL_BYTES = 5 * 1024
PHASE2_MAX_ITERATIONS = 5  # v4 §8.2


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
    workspace_root: Path | None = None
    cancel_token: asyncio.Event = field(default_factory=asyncio.Event)
    logger: structlog.stdlib.BoundLogger = field(
        default_factory=lambda: structlog.get_logger("orchestrator")
    )

    async def start(self, user_input: str) -> SystemState:
        raise NotImplementedError("Orchestrator.start — phase 2 구현 예정 (v4 §6.1)")

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
        """Phase L: ProcessManager.launch + health-check."""
        raise NotImplementedError("Orchestrator._run_launching — phase 2 구현 예정")

    async def _run_testing(self, state: SystemState) -> SystemState:
        """Phase 3: Evaluator.testrun ↔ Executor revise loop."""
        raise NotImplementedError("Orchestrator._run_testing — phase 2 구현 예정")

    async def _teardown(self, state: SystemState) -> SystemState:
        """SIGTERM (grace 5s) → SIGKILL → port release (v4 §18)."""
        raise NotImplementedError("Orchestrator._teardown — phase 2 구현 예정")


# --- frontmatter 파싱 utilities ---

# 문서 시작의 `---\n ... \n---\n` 블록만 인식. 본문 중간의 `---` 는 무시.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)

# Fallback 정규식: 본문 헤더 형식 `## U-NNN: title` / `* U-NNN: title` / `- U-NNN: title`.
_UNIT_HEADER_RE = re.compile(r"^[#*\-]+\s*(U-\d+)\s*[:\-]\s*(.+?)\s*$", re.MULTILINE)


def _extract_frontmatter(text: str) -> str | None:
    """문서 처음의 YAML frontmatter 본문 (`---` 사이) 반환. 없으면 None."""
    m = _FRONTMATTER_RE.match(text)
    return m.group(1) if m else None


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
